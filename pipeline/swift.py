"""
SWiFT-Bank: Statistically tested sub-pixel Water-Fraction Time series for bank erosion.

A new method for mapping river bank erosion smaller than one image pixel, with a
measure of confidence for every reported loss. It is run for Dibrugarh district
and compared with the standard 20 m method and the 10 m sub-pixel frequency method.

Method
  1. Water fraction on every clear date. Each 10 m pixel is modelled as a mixture
     of water and land. The water and land end-members are taken from pure pixels
     around it (within 155 m, or 755 m where pure pixels are few), separately for
     each date, so they follow local sediment load, sand and vegetation. The water
     fraction is the projection of the pixel spectrum (green, NIR and sharpened SWIR)
     on the line between the two end-members.
  2. Per-pixel statistics. For each monitoring window the mean and variance of the
     water fraction over all clear dates are kept, so each pixel has a sample of
     fractions, not one label.
  3. Tested change. The change in mean water fraction between the two windows is
     tested with Welch's t-test, and the false discovery rate over the corridor is
     controlled at 5 % (Benjamini and Hochberg, 1995). A pixel is a loss pixel when
     the change is significant and at least 0.1 (10 m2 of land in a 100 m2 pixel).
  4. Loss area below the pixel size. The land lost in a patch is the sum of the
     changes in water fraction times the pixel area, so a patch can be smaller than
     its pixel footprint. Each patch gets a p-value by combining the pixel tests
     (Stouffer's method, deflated for the 2 x 2 correlation of sharpened pixels).
     Patches with at least 0.02 ha of lost land and p < 0.01 are reported.
  5. Detection limit map. For every pixel the minimum detectable change in land
     area (5 % test, 80 % power) is computed from its own noise, so the map shows
     where small losses can and cannot be seen.
  6. Null test. The clear dates of the current window are split into two halves
     (odd and even dates). No real erosion happens between them, so any change found
     is a false alarm. The same test is run on the 10 m and 20 m frequency methods.
  7. Sub-pixel bank lines are the 0.5 contour of the mean water fraction.
  8. Test of the baseline maps. The two frequency baselines have no test of their own.
     As a check, the water and land date counts of every pixel in the two windows are
     compared with Fisher's exact test (false discovery rate 5 %), the pixel tests are
     combined over each baseline loss patch, and the share of patches with p < 0.01 is
     reported. The 20 m counts come from the same dates, with green and SWIR averaged
     over 2 x 2 pixels, which gives the native 20 m bands.

Run:  python pipeline/swift.py --only baseline   (composite job)
      python pipeline/swift.py --only current
      python pipeline/swift.py                   (comparison)
"""
import argparse
import json
import time
import warnings
from pathlib import Path

import numpy as np
import requests
import rioxarray  # noqa: F401
import xarray as xr
import yaml
from scipy import ndimage, stats

import analysis as an
import hires as hi
from acquire import CHUNKS, S2_CLEAR_SCL, SIGN, _clearest, _s2_offset, _search, get_aoi, stac_load

RES = 10
BLOCK = 1024
PURE_WATER = 0.30            # MNDWI above this: pure water
PURE_LAND = -0.20            # MNDWI below this: pure land
WIN_SMALL, WIN_LARGE = 31, 151
MIN_PURE = 10                # pure pixels needed in a window
SIGMA0 = 0.05                # noise floor of a single water-fraction estimate
MIN_EFFECT = 0.10            # minimum change in water fraction per pixel
FDR_Q = 0.05
PATCH_P = 0.01
MMU_HA = 0.02                # minimum lost land per patch
THRESHOLDS = [0.0, 0.05, 0.10, 0.15, 0.20]   # MNDWI thresholds kept for the frequency method
SIZE_BINS = [0.02, 0.05, 0.1, 0.2, 0.4, 1, 5, 20, 1e9]
SIZE_LABELS = ["0.02–0.05", "0.05–0.1", "0.1–0.2", "0.2–0.4", "0.4–1", "1–5", "5–20", "> 20"]
CLASSES = hi.CLASSES
COLORS = hi.COLORS
MDC_COLORS = {1: "#1a9850", 2: "#91cf60", 3: "#fee08b", 4: "#fc8d59", 5: "#d73027"}
MDC_BINS = [0, 15, 25, 40, 70, 1e9]
MDC_LABELS = ["< 15 m²", "15–25 m²", "25–40 m²", "40–70 m²", "> 70 m²"]


def tkey(t):
    return f"{t:+.2f}"


# ------------------------------------------------------------ water fraction
def _local_mean(X, mask, win):
    """Mean of each band over the masked pixels in a square window, and the count."""
    m = mask.astype("float32")
    cnt = ndimage.uniform_filter(m, win, mode="nearest") * (win * win)
    out = np.empty_like(X)
    for b in range(X.shape[0]):
        s = ndimage.uniform_filter(np.where(mask, X[b], 0).astype("float32"), win, mode="nearest") * (win * win)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[b] = s / cnt
    return out, cnt


def _endmember(X, mask):
    """Local end-member: small window where possible, larger window, then the block median."""
    e1, c1 = _local_mean(X, mask, WIN_SMALL)
    e2, c2 = _local_mean(X, mask, WIN_LARGE)
    e = np.where(c1 >= MIN_PURE, e1, e2)
    weak = c2 < MIN_PURE
    if weak.any():
        if mask.sum() >= MIN_PURE:
            med = np.array([np.median(X[b][mask]) for b in range(X.shape[0])], "float32")
            e = np.where(weak[None], med[:, None, None], e)
        else:
            e = np.where(weak[None], np.nan, e)
    return e


def water_fraction(g, n, s):
    """Water fraction of every pixel on one date. g, n, s: green, NIR, sharpened SWIR."""
    X = np.stack([g, n, s]).astype("float32")
    valid = np.isfinite(X).all(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = (g - s) / (g + s)
    if valid.sum() < 1000:
        return np.full(g.shape, np.nan, "float32"), m
    Xz = np.where(valid[None], X, 0)
    W = _endmember(Xz, valid & (m > PURE_WATER))
    L = _endmember(Xz, valid & (m < PURE_LAND))
    d = W - L
    dd = (d * d).sum(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        f = ((Xz - L) * d).sum(0) / dd
    ok = valid & np.isfinite(f) & (dd > 0.002)
    return np.where(ok, np.clip(f, 0, 1), np.nan).astype("float32"), np.where(valid, m, np.nan)


def block_stats(g, n, s, subsets):
    """Per-pixel statistics of the water fraction (and MNDWI counts) for date subsets."""
    T = g.shape[0]
    F = np.full(g.shape, np.nan, "float32")
    M = np.full(g.shape, np.nan, "float32")
    for t in range(T):
        F[t], M[t] = water_fraction(g[t], n[t], s[t])
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for name, idx in subsets.items():
            f, m = F[idx], M[idx]
            k = np.isfinite(f).sum(0)
            out[f"{name}_n"] = k.astype("uint8")
            out[f"{name}_mean"] = np.where(k > 0, np.nanmean(f, 0), np.nan).astype("float32")
            out[f"{name}_var"] = np.where(k > 1, np.nanvar(f, 0, ddof=1), np.nan).astype("float32")
            ok = np.isfinite(m)
            out[f"{name}_nm"] = ok.sum(0).astype("uint8")
            for th in THRESHOLDS:
                out[f"{name}_k{tkey(th)}"] = (ok & (m > th)).sum(0).astype("uint8")
        # 20 m baseline: green and SWIR averaged over 2 x 2 pixels (the native 20 m bands)
        M20 = mndwi20(g, s)
        for name, idx in subsets.items():
            m = M20[idx]
            ok = np.isfinite(m)
            out[f"m20_{name}_nm"] = ok.sum(0).astype("uint8")
            for th in THRESHOLDS:
                out[f"m20_{name}_k{tkey(th)}"] = (ok & (m > th)).sum(0).astype("uint8")
    return out


def _half(a):
    """Mean over 2 x 2 pixels (NaN if any of the four is missing); odd edges are padded."""
    T, h, w = a.shape
    a = np.pad(a, ((0, 0), (0, h % 2), (0, w % 2)), constant_values=np.nan)
    return a.reshape(T, a.shape[1] // 2, 2, a.shape[2] // 2, 2).mean(axis=(2, 4))


def mndwi20(g, s):
    g2, s2 = _half(g.astype("float32")), _half(s.astype("float32"))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (g2 - s2) / (g2 + s2)


def period_stats(bbox_ll, period, crs, max_cloud, per_tile, cmask, split):
    items = _search("sentinel-2-l2a", bbox_ll, period, {"eo:cloud_cover": {"lt": max_cloud}})
    items = _clearest(list(items), per_tile)
    offs = [_s2_offset(it) for it in items]
    kw = dict(bands=["B03", "B08", "B11", "SCL"], bbox=bbox_ll, crs=crs, resolution=RES, chunks=CHUNKS,
              fail_on_error=False, patch_url=SIGN, resampling={"B11": "bilinear", "*": "nearest"})
    if len(set(offs)) == 1:
        ds = stac_load(items, groupby="solar_day", **kw)
        off = np.float32(offs[0])
    else:
        ds = stac_load(items, **kw)
        off = xr.DataArray(np.array(offs, "float32"), dims="time")
    clear = ds["SCL"].isin(S2_CLEAR_SCL)
    refl = {b: ((ds[b].astype("float32").where((ds[b] > 0) & clear) + off) / np.float32(1e4)).clip(0, 1)
            for b in ["B03", "B08", "B11"]}
    nir = refl["B08"]
    nir20 = xr.apply_ufunc(hi._nir20, nir, dask="parallelized", output_dtypes=[np.float32])
    swir = (refl["B11"] * (nir / nir20)).clip(0, 1)
    lazy = xr.Dataset({"g": refl["B03"], "n": nir, "s": swir})
    T, H, W = lazy.sizes["time"], lazy.sizes["y"], lazy.sizes["x"]
    idx = np.arange(T)
    subsets = {"all": idx}
    if split:
        subsets.update({"odd": idx[0::2], "even": idx[1::2]})
    print(f"  {T} dates; subsets: " + ", ".join(f"{k} {len(v)}" for k, v in subsets.items()), flush=True)

    from rasterio.enums import Resampling
    tmpl = xr.DataArray(np.zeros((H, W), "uint8"), coords={"y": lazy.y, "x": lazy.x}, dims=("y", "x")).rio.write_crs(crs)
    need = cmask.rio.reproject_match(tmpl, resampling=Resampling.max).values > 0
    blocks = [(i, j) for i in range(0, H, BLOCK) for j in range(0, W, BLOCK) if need[i:i + BLOCK, j:j + BLOCK].any()]
    print(f"  {len(blocks)} blocks of {BLOCK} px touch the corridor", flush=True)
    arrs = None
    for k, (i, j) in enumerate(blocks, 1):
        part = lazy.isel(y=slice(i, i + BLOCK), x=slice(j, j + BLOCK)).compute()
        res = block_stats(part["g"].values, part["n"].values, part["s"].values, subsets)
        if arrs is None:
            shp = lambda v: ((H + 1) // 2, (W + 1) // 2) if v.startswith("m20_") else (H, W)
            arrs = {v: (np.full(shp(v), np.nan, a.dtype) if a.dtype.kind == "f" else np.zeros(shp(v), a.dtype))
                    for v, a in res.items()}
        for v, a in res.items():
            if v.startswith("m20_"):
                arrs[v][i // 2:i // 2 + a.shape[0], j // 2:j // 2 + a.shape[1]] = a
            else:
                arrs[v][i:i + BLOCK, j:j + BLOCK] = a
        if k % 5 == 0 or k == len(blocks):
            print(f"  water fraction {period[0][:7]}: block {k} of {len(blocks)} done", flush=True)
    arrs["x"] = lazy.x.values
    arrs["y"] = lazy.y.values
    return arrs


# --------------------------------------------------------------------- tests
def welch(na, ma, va, nb, mb, vb):
    """Welch t-test of mean(b) - mean(a); returns difference, standard error, one- and two-sided p."""
    na = na.astype("float64")
    nb = nb.astype("float64")
    va = np.maximum(np.nan_to_num(va, nan=0.0), SIGMA0 ** 2)
    vb = np.maximum(np.nan_to_num(vb, nan=0.0), SIGMA0 ** 2)
    sa, sb = va / na, vb / nb
    se = np.sqrt(sa + sb)
    d = (mb - ma).astype("float64")
    df = (sa + sb) ** 2 / (sa ** 2 / (na - 1) + sb ** 2 / (nb - 1))
    tt = d / se
    p_up = stats.t.sf(tt, df)          # water fraction increased (land lost)
    p_two = 2 * stats.t.sf(np.abs(tt), df)
    return d, se, p_up, p_two


def bh_reject(p, q):
    """Benjamini-Hochberg: boolean array of rejected hypotheses."""
    p = np.asarray(p)
    m = p.size
    if m == 0:
        return np.zeros(0, bool)
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    below = np.nonzero(ranked <= q)[0]
    rej = np.zeros(m, bool)
    if below.size:
        rej[order[:below.max() + 1]] = True
    return rej


def tested_change(A, B, inside, min_n=3):
    """Test the change of mean water fraction from statistics A to B on the corridor pixels.
    Returns full-grid arrays: d (change), z (one-sided, land loss), sig (FDR), se."""
    ok = inside & (A["n"] >= min_n) & (B["n"] >= min_n) & np.isfinite(A["mean"]) & np.isfinite(B["mean"])
    sel = np.nonzero(ok.ravel())[0]
    flat = lambda a: a.ravel()[sel]
    d, se, p_up, p_two = welch(flat(A["n"]), flat(A["mean"]), flat(A["var"]),
                               flat(B["n"]), flat(B["mean"]), flat(B["var"]))
    rej = bh_reject(p_two, FDR_Q)
    shape = inside.shape
    D = np.full(shape, np.nan, "float32"); D.ravel()[sel] = d
    SE = np.full(shape, np.nan, "float32"); SE.ravel()[sel] = se
    Z = np.zeros(shape, "float32"); Z.ravel()[sel] = np.clip(stats.norm.isf(np.clip(p_up, 1e-300, 1)), -40, 40)
    S = np.zeros(shape, bool); S.ravel()[sel] = rej
    return {"d": D, "se": SE, "z": Z, "sig": S, "tested": int(sel.size), "rejected": int(rej.sum())}


def loss_patches(T, sign=1):
    """Connected loss (sign=1) or gain (sign=-1) patches with lost-land area and a combined p-value."""
    d = np.nan_to_num(T["d"]) * sign
    px = T["sig"] & (d >= MIN_EFFECT)
    lab, n = ndimage.label(px, structure=np.ones((3, 3)))
    if n == 0:
        return lab, np.zeros(0), np.zeros(0), np.zeros(0, int)
    idx = np.arange(1, n + 1)
    area_ha = ndimage.sum(d, lab, idx) * RES * RES / 1e4
    k = ndimage.sum(np.ones_like(d), lab, idx)
    zz = T["z"] * sign
    zsum = ndimage.sum(zz, lab, idx)
    zp = zsum / (2 * np.sqrt(k))                     # Stouffer, deflated for 2 x 2 correlation
    p = stats.norm.sf(zp)
    keep = (area_ha >= MMU_HA) & (p < PATCH_P)
    return lab, area_ha, p, keep


def kept_mask(lab, keep):
    lut = np.zeros(len(keep) + 1, bool)
    lut[1:] = keep
    return lut[lab]


def freq_change(Ka, Na, Kb, Nb, inside, min_px=5):
    """10 m frequency method: water if water on at least half of the clear dates; changed pixels after sieving."""
    with np.errstate(invalid="ignore", divide="ignore"):
        wa = (Ka / np.where(Na > 0, Na, np.nan)) >= 0.5
        wb = (Kb / np.where(Nb > 0, Nb, np.nan)) >= 0.5
    ok = inside & (Na >= 2) & (Nb >= 2)
    loss = ok & ~wa & wb
    gain = ok & wa & ~wb
    loss = hi.sieve(loss.astype("uint8"), min_px) == 1
    gain = hi.sieve(gain.astype("uint8"), min_px) == 1
    return loss, gain, wa, wb


def count_test(Ka, Na, Kb, Nb, ok):
    """Fisher's exact test of the water-date counts of each pixel (window A against B).
    Returns z (one-sided, more water in B = land loss) and FDR significance."""
    sel = np.nonzero((ok & (Na >= 2) & (Nb >= 2)).ravel())[0]
    ka, na = Ka.ravel()[sel].astype(int), Na.ravel()[sel].astype(int)
    kb, nb = Kb.ravel()[sel].astype(int), Nb.ravel()[sel].astype(int)
    p_up = stats.hypergeom.sf(kb - 1, na + nb, ka + kb, nb)
    p_dn = stats.hypergeom.cdf(kb, na + nb, ka + kb, nb)
    p_two = np.minimum(1.0, 2 * np.minimum(p_up, p_dn))
    rej = bh_reject(p_two, FDR_Q)
    Z = np.zeros(Ka.shape, "float32"); Z.ravel()[sel] = np.clip(stats.norm.isf(np.clip(p_up, 1e-300, 1)), -40, 40)
    S = np.zeros(Ka.shape, bool); S.ravel()[sel] = rej
    return Z, S


def baseline_patch_test(loss, Z, S, px_ha, deflate):
    """Combine the pixel tests over each baseline loss patch (Stouffer). A patch passes when
    p < PATCH_P and at least one of its pixels is significant after FDR."""
    lab, n = ndimage.label(loss, structure=np.ones((3, 3)))
    if n == 0:
        return {"patches": 0, "significant": 0, "share_patches": None, "share_area": None, "loss_ha": 0.0}
    idx = np.arange(1, n + 1)
    k = ndimage.sum(np.ones(loss.shape, "float32"), lab, idx)
    p = stats.norm.sf(ndimage.sum(Z, lab, idx) / (deflate * np.sqrt(k)))
    anysig = ndimage.maximum(S.astype("uint8"), lab, idx) > 0
    ok = (p < PATCH_P) & anysig
    return {"patches": int(n), "significant": int(ok.sum()), "share_patches": round(float(ok.mean()), 3),
            "share_area": round(float(k[ok].sum() / k.sum()), 3), "loss_ha": round(float(k.sum() * px_ha), 1),
            "loss_ha_significant": round(float(k[ok].sum() * px_ha), 1)}


# ---------------------------------------------------------------------- main
def load(raw, tag, end):
    z = np.load(raw / f"swift_{tag}_{end}.npz")
    return {k: z[k] for k in z.files}


def sub(S, name):
    return {"n": S[f"{name}_n"], "mean": S[f"{name}_mean"], "var": S[f"{name}_var"]}


def hist(areas):
    h, _ = np.histogram(np.asarray(areas, float), bins=SIZE_BINS)
    return [int(v) for v in h]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--only", choices=["baseline", "current"])
    args = ap.parse_args()
    t0 = time.time()
    cfg = yaml.safe_load(Path(args.config).read_text())
    pubroot = Path(cfg["publish_dir"])
    pub = pubroot / "swift"
    pub.mkdir(parents=True, exist_ok=True)
    summ = json.loads((pubroot / "summary.json").read_text())
    periods = {"baseline": summ["period_baseline"], "current": summ["period_current"]}
    print("Periods:", periods)
    aoi, _ = get_aoi(cfg)
    cmask, bounds = hi.corridor(cfg, aoi)
    bbox_ll = hi.to_ll(bounds, cfg["crs"])
    raw = Path(cfg["output_dir"]) / cfg["project_name"] / "swift"
    raw.mkdir(parents=True, exist_ok=True)

    if args.only:
        per = periods[args.only]
        f = raw / f"swift_{args.only}_{per[1]}.npz"
        if f.exists() and "m20_all_nm" in np.load(f).files:
            print("  already built:", f.name)
            return
        arrs = period_stats(bbox_ll, per, cfg["crs"], cfg["max_cloud_cover"], cfg.get("max_scenes_per_tile", 15),
                            cmask, split=(args.only == "current"))
        np.savez_compressed(f, **arrs)
        print(f"  saved {f.name} in {(time.time() - t0) / 60:.1f} min")
        return

    # ---------------- comparison
    B = load(raw, "baseline", periods["baseline"][1])
    C = load(raw, "current", periods["current"][1])
    x, y = C["x"], C["y"]
    H, W = len(y), len(x)
    tmpl = xr.DataArray(np.zeros((H, W), "uint8"), coords={"y": y, "x": x}, dims=("y", "x")).rio.write_crs(cfg["crs"])
    tr = tmpl.rio.transform()
    from rasterio.enums import Resampling
    from rasterio.features import geometry_mask
    inside = ~geometry_mask(aoi.to_crs(cfg["crs"]).geometry, out_shape=(H, W), transform=tr)
    inside &= cmask.rio.reproject_match(tmpl, resampling=Resampling.nearest).values.astype(bool)
    px_ha = RES * RES / 1e4
    print(f"  grid {W} x {H}; corridor {inside.sum() * px_ha / 100:.0f} km2")

    # 1. Tested change between the windows
    T = tested_change(sub(B, "all"), sub(C, "all"), inside)
    print(f"  tested {T['tested']} pixels, {T['rejected']} significant after FDR")
    lab, area_ha, pval, keep = loss_patches(T, 1)
    glab, garea, gp, gkeep = loss_patches(T, -1)
    loss_px = kept_mask(lab, keep)
    gain_px = kept_mask(glab, gkeep)
    wb = np.nan_to_num(B["all_mean"]) >= 0.5
    jrc = an.jrc_transitions(tmpl, cfg.get("history_raster"))
    c = np.zeros((H, W), "uint8")
    c[loss_px] = 1
    c[gain_px] = 2
    if jrc is not None:
        c[(c == 1) & (jrc >= 1) & (jrc <= 10)] = 5
    chg = an.split_inland_water(tmpl.copy(data=c), tmpl.copy(data=wb), RES, 50.0, touch_px=4)
    c = chg.values
    d = np.nan_to_num(T["d"])
    area = {CLASSES[k]: round(float(np.abs(d[c == k]).sum() * px_ha), 1) for k in CLASSES}

    # Patch table (bank erosion and char loss only)
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape
    bank = np.isin(c, [1, 5])
    plab, npatch = ndimage.label(bank, structure=np.ones((3, 3)))
    recs = []
    if npatch:
        idx = np.arange(1, npatch + 1)
        p_area = ndimage.sum(d, plab, idx) * px_ha
        p_k = ndimage.sum(np.ones_like(d), plab, idx)
        p_z = ndimage.sum(T["z"], plab, idx) / (2 * np.sqrt(p_k))
        p_p = stats.norm.sf(p_z)
        n5 = ndimage.sum((c == 5).astype("float32"), plab, idx)
        p_class = [CLASSES[5] if v5 > kk / 2 else CLASSES[1] for v5, kk in zip(n5, p_k)]
        p_se = ndimage.mean(np.nan_to_num(T["se"]), plab, idx)
        for g, v in shapes(plab.astype("int32"), mask=plab > 0, transform=tr, connectivity=8):
            i = int(v) - 1
            recs.append({"geometry": shape(g), "lab": i, "loss_ha": float(p_area[i]), "footprint_ha": float(p_k[i] * px_ha),
                         "p_value": float(p_p[i]), "class": p_class[i], "mean_change": float(p_area[i] / (p_k[i] * px_ha)),
                         "se_mean": float(p_se[i])})
    P = gpd.GeoDataFrame(recs, geometry="geometry", crs=cfg["crs"]) if recs else gpd.GeoDataFrame(
        {"loss_ha": []}, geometry=[], crs=cfg["crs"])
    if len(P):
        P = P.dissolve(by="lab", aggfunc="first").reset_index(drop=True)
    print(f"  {len(P)} bank and char loss patches")

    # 2. Sub-pixel bank lines and retreat from the water fraction
    river_b = an.river_mask(wb & inside, RES, 50.0)
    wc = np.nan_to_num(C["all_mean"]) >= 0.5
    river_c = an.river_mask(wc & inside, RES, 50.0)
    lb = hi.bank_lines(np.where(inside, np.nan_to_num(B["all_mean"]), 0), river_b, tr)
    lc = hi.bank_lines(np.where(inside, np.nan_to_num(C["all_mean"]), 0), river_c, tr)
    if len(P):
        P["retreat_subpixel_m"] = hi.subpixel_retreat(P, lb, lc)

    # 3. Detection limit (minimum detectable change in land area per pixel, 5 % two-sided, 80 % power)
    mdc_m2 = (stats.norm.isf(0.025) + stats.norm.isf(0.2)) * T["se"] * RES * RES
    edge = ndimage.binary_dilation(river_b, iterations=5) & ~river_b & inside
    mdc_bank = mdc_m2[edge & np.isfinite(mdc_m2)]
    mdc_cls = np.digitize(np.nan_to_num(mdc_m2, nan=-1), MDC_BINS[1:-1]) + 1
    mdc_cls = np.where(edge & np.isfinite(mdc_m2), mdc_cls, 0).astype("uint8")

    # 4. Null test: odd against even dates of the current window
    N = tested_change(sub(C, "odd"), sub(C, "even"), inside)
    nlab, narea, npv, nkeep = loss_patches(N, 1)
    nglab, ngarea, ngp, ngkeep = loss_patches(N, -1)
    null_swift_ha = float(narea[nkeep].sum() + ngarea[ngkeep].sum()) if len(nkeep) or len(ngkeep) else 0.0
    null_swift_n = int(nkeep.sum() + ngkeep.sum())
    null_swift_fp = float((kept_mask(nlab, nkeep) | kept_mask(nglab, ngkeep)).sum() * px_ha)
    hs = json.loads((pubroot / "hires" / "summary.json").read_text()) if (pubroot / "hires" / "summary.json").exists() else {}
    th = hs.get("water", {}).get("current", {}).get("threshold", 0.1)
    th = min(THRESHOLDS, key=lambda v: abs(v - th))
    fl, fg, _, _ = freq_change(C[f"odd_k{tkey(th)}"], C["odd_nm"], C[f"even_k{tkey(th)}"], C["even_nm"], inside)
    fpl = ndimage.label(fl | fg, structure=np.ones((3, 3)))[1]
    null_freq_ha = float((fl | fg).sum() * px_ha)
    print(f"  null test: SWiFT {null_swift_n} patches {null_swift_ha:.2f} ha; frequency {fpl} patches {null_freq_ha:.2f} ha")

    # 5. Agreement with the 10 m frequency method (recomputed on the same grid) and the 20 m release
    tb = hs.get("water", {}).get("baseline", {}).get("threshold", 0.1)
    tb = min(THRESHOLDS, key=lambda v: abs(v - tb))
    floss, _, _, _ = freq_change(B[f"all_k{tkey(tb)}"], B["all_nm"], C[f"all_k{tkey(th)}"], C["all_nm"], inside)
    sl = bank | (c == 6)
    agree = {"share_freq10_loss_found": round(float((floss & ndimage.binary_dilation(sl, iterations=2)).sum() / max(floss.sum(), 1)), 3),
             "share_swift_loss_in_freq10": round(float((sl & ndimage.binary_dilation(floss, iterations=2)).sum() / max(sl.sum(), 1)), 3)}
    try:
        repo = summ.get("repository", "")
        r = requests.get(f"https://github.com/{repo}/releases/latest/download/change_class.tif", timeout=120)
        r.raise_for_status()
        p20 = raw / "change_class_20m.tif"
        p20.write_bytes(r.content)
        c20n = rioxarray.open_rasterio(p20).squeeze("band", drop=True)
        rep20 = hi.sieve(np.isin(c20n.values, [1, 5]).astype("uint8"), 10)
        rep20 = c20n.copy(data=rep20).rio.reproject_match(tmpl, resampling=Resampling.nearest).values == 1
        rep20 &= inside
        agree["share_std20_loss_found"] = round(float((rep20 & ndimage.binary_dilation(bank, iterations=2)).sum() / max(rep20.sum(), 1)), 3)
    except Exception as e:
        print("  20 m release not read:", e)

    # 6. 20 m frequency baseline on the same dates: null test, and the test of both baselines
    t20 = summ.get("water_detection", {})
    tb20 = min(THRESHOLDS, key=lambda v: abs(v - t20.get("baseline", {}).get("mndwi_threshold_used", 0.1)))
    tc20 = min(THRESHOLDS, key=lambda v: abs(v - t20.get("current", {}).get("mndwi_threshold_used", 0.1)))
    btests = {"method": "Fisher exact test of water and land date counts per pixel; FDR 5 %; patch p < 0.01",
              "thresholds": {"freq10": [tb, th], "freq20": [tb20, tc20]}}
    try:
        Z10, S10 = count_test(B[f"all_k{tkey(tb)}"], B["all_nm"], C[f"all_k{tkey(th)}"], C["all_nm"], inside)
        btests["freq10"] = baseline_patch_test(floss, Z10, S10, px_ha, 2.0)
        if "m20_all_nm" in C and "m20_all_nm" in B:
            h2, w2 = C["m20_all_nm"].shape
            ins = np.pad(inside, ((0, 2 * h2 - H), (0, 2 * w2 - W)))
            inside20 = ins.reshape(h2, 2, w2, 2).sum(axis=(1, 3)) >= 2
            px20 = 0.04
            n20l, n20g, _, _ = freq_change(C[f"m20_odd_k{tkey(tc20)}"], C["m20_odd_nm"], C[f"m20_even_k{tkey(tc20)}"],
                                           C["m20_even_nm"], inside20, min_px=10)
            null_f20_n = int(ndimage.label(n20l | n20g, structure=np.ones((3, 3)))[1])
            null_f20_ha = float((n20l | n20g).sum() * px20)
            print(f"  null test 20 m: {null_f20_n} patches {null_f20_ha:.2f} ha")
            f20l, _, _, _ = freq_change(B[f"m20_all_k{tkey(tb20)}"], B["m20_all_nm"], C[f"m20_all_k{tkey(tc20)}"],
                                        C["m20_all_nm"], inside20, min_px=10)
            Z20, S20 = count_test(B[f"m20_all_k{tkey(tb20)}"], B["m20_all_nm"], C[f"m20_all_k{tkey(tc20)}"],
                                  C["m20_all_nm"], inside20)
            btests["freq20"] = baseline_patch_test(f20l, Z20, S20, px20, 1.0)
        else:
            null_f20_n, null_f20_ha = None, None
        print("  baseline tests:", json.dumps({k: v for k, v in btests.items() if k.startswith("freq")}))
    except Exception as e:
        print("  baseline tests failed:", e)
        null_f20_n, null_f20_ha = None, None

    small = P[P.loss_ha < 0.4] if len(P) else P
    out = {"run": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "periods": periods,
           "method": "SWiFT-Bank", "resolution_m": RES, "min_patch_ha": MMU_HA,
           "area_analysed_km2": round(float(inside.sum() * px_ha / 100), 1),
           "settings": {"fdr_q": FDR_Q, "patch_p": PATCH_P, "min_effect": MIN_EFFECT, "sigma0": SIGMA0,
                        "pure_water_mndwi": PURE_WATER, "pure_land_mndwi": PURE_LAND,
                        "window_m": [WIN_SMALL * RES, WIN_LARGE * RES]},
           "tests": {"pixels_tested": T["tested"], "pixels_significant": T["rejected"]},
           "area_ha": area,
           "bank_loss_patches": int(len(P)),
           "bank_loss_ha": round(float(P.loss_ha.sum()), 1) if len(P) else 0.0,
           "bank_footprint_ha": round(float(P.footprint_ha.sum()), 1) if len(P) else 0.0,
           "small_patches": int(len(small)), "small_ha": round(float(small.loss_ha.sum()), 2) if len(small) else 0.0,
           "patch_p_median": float(np.median(P.p_value)) if len(P) else None,
           "size_hist": hist(P.loss_ha if len(P) else []), "size_labels": SIZE_LABELS,
           "mdc_bank_m2": {"median": round(float(np.median(mdc_bank)), 1) if mdc_bank.size else None,
                           "p10": round(float(np.percentile(mdc_bank, 10)), 1) if mdc_bank.size else None,
                           "p90": round(float(np.percentile(mdc_bank, 90)), 1) if mdc_bank.size else None,
                           "share_below_25m2": round(float((mdc_bank < 25).mean()), 3) if mdc_bank.size else None},
           "null_test": {"swift_patches": null_swift_n, "swift_ha": round(null_swift_ha, 2), "swift_footprint_ha": round(null_swift_fp, 2),
                         "freq10_patches": int(fpl), "freq10_ha": round(null_freq_ha, 2),
                         "freq20_patches": null_f20_n, "freq20_ha": None if null_f20_ha is None else round(null_f20_ha, 2),
                         "dates_odd": int(np.nanmax(C["odd_n"])), "dates_even": int(np.nanmax(C["even_n"])),
                         "threshold_freq10": th},
           "agreement": agree,
           "baseline_tests": btests,
           "bankline_km": {}, "legend": [[CLASSES[k], COLORS[k]] for k in [1, 5, 6, 2]],
           "mdc_legend": [[MDC_LABELS[i], MDC_COLORS[i + 1]] for i in range(5)]}
    if len(P) and np.isfinite(P["retreat_subpixel_m"]).any():
        out["retreat"] = {"median_subpixel_m": round(float(np.nanmedian(P["retreat_subpixel_m"])), 1)}
    out["layers"] = {"swift": {"file": "swift/change.png", "bounds": hi.png(chg, COLORS, pub / "change.png")},
                     "mdc": {"file": "swift/mdc.png", "bounds": hi.png(tmpl.copy(data=mdc_cls), MDC_COLORS, pub / "mdc.png")}}
    out["bankline_km"] = {"baseline": hi.lines_geojson(lb, cfg["crs"], pub / "banklines_baseline.geojson"),
                          "current": hi.lines_geojson(lc, cfg["crs"], pub / "banklines_current.geojson")}
    if len(P):
        g = P.copy()
        g["geometry"] = g.geometry.simplify(2)
        g = g.to_crs(4326)
        for k in ["loss_ha", "footprint_ha"]:
            g[k] = g[k].round(4)
        g["mean_change"] = g["mean_change"].round(3)
        g["se_mean"] = g["se_mean"].round(3)
        g["p_value"] = g["p_value"].map(lambda v: float(f"{v:.2e}"))
        g["small"] = g["loss_ha"] < 0.4
        cent = g.geometry.representative_point()
        g["lat"], g["lon"] = cent.y.round(6), cent.x.round(6)
        g["geometry"] = g.geometry.set_precision(1e-6)
        (pub / "loss_patches.geojson").write_text(g.to_json(drop_id=True))
    out["runtime_min"] = round((time.time() - t0) / 60, 1)
    (pub / "summary.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "layers"}, indent=2))

    # SWiFT-Bank edition of the full district dashboard (docs/swift/)
    import swift_system
    swift_system.build(cfg, {"raw": raw, "c": c, "d": d, "z": T["z"], "tmpl": tmpl, "inside": inside, "P": P,
                             "cmask": cmask, "aoi": aoi, "out": out, "lb": lb, "lc": lc})

    # Download files (GeoTIFF and Shapefile), published as the release "swift-latest"
    try:
        import exports
        exports.swift_files(cfg, {"tmpl": tmpl, "inside": inside, "c": c, "d": d,
                                  "wf_base": B["all_mean"], "wf_cur": C["all_mean"], "mdc_m2": mdc_m2, "P": P,
                                  "lb": lb, "lc": lc, "periods": periods, "pub": pub,
                                  "dash": pubroot.parent / "swift" / "data", "repository": summ.get("repository", "")})
    except Exception as e:
        print("  download files not written:", e)


if __name__ == "__main__":
    main()
