"""
Independent accuracy checks of the three water and bank-change methods with
labelled data that the methods did not produce.

Test 1. Water detection on single Sentinel-2 dates.
  Reference: Dynamic World expert-consensus test tiles (Brown et al., 2022;
  Zenodo record 4766451, CC BY 4.0): 409 tiles of 5.1 x 5.1 km, labelled by
  hand at 10 m by several experts on a named Sentinel-2 L2A scene. One tile lies
  on the Brahmaputra at Majuli (19 January 2019).
  For every tile with both water and land labels, the same Sentinel-2 scene is
  read from the Planetary Computer and classified by
    M1  standard     MNDWI at 20 m, edge-based Otsu threshold
    M2  10 m         MNDWI at 10 m with the sharpened SWIR band, edge-based Otsu
    M3  SWiFT-Bank   water fraction from local end-members, water if f >= 0.5
  and compared with the labels (overall accuracy, water F1 and IoU, and accuracy
  within 20 m of the labelled shoreline, where bank change happens).
  Sub-pixel test: the 10 m labels are averaged to 20 m and 30 m blocks, which
  gives the true water fraction of each block. The reflectances are averaged to
  the same blocks and SWiFT-Bank estimates the fraction from them; the hard
  classification counts a block as all water or all land. The error of the
  fraction in mixed blocks shows whether the sub-pixel estimate is real.
  Confidence intervals are from a bootstrap over tiles.

Test 2. Bank erosion rates against a global Landsat product.
  Reference: Riverbank Erosion and Accretion from Landsat (REAL; Langhorst and
  Pavelsky; Zenodo record 7045021, CC BY 4.0): erosion rate per SWORD river reach
  from about 20 years of Landsat water maps. The land lost in each reach by each
  method (2019-20 to 2025-26) is turned into a bank retreat rate and ranked
  against REAL (Spearman correlation). The periods differ, so this is a test of
  consistency with an independent long-term record, not of accuracy.

Run in GitHub Actions: python pipeline/external_validation.py
Writes docs/data/external/.
"""
import io
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import rioxarray  # noqa: F401
import xarray as xr
from scipy import ndimage, stats

import analysis as an
import hires as hi
import swift as sw
from acquire import S2_CLEAR_SCL, SIGN, _catalog, _s2_offset, stac_load

OUT = Path("docs/data/external")
WORK = Path("outputs/external")
DW_URL = "https://zenodo.org/api/records/4766451/files/dw_expert_consensus_tiles.zip/content"
REAL_URL = "https://zenodo.org/api/records/7045021/files/{}/content"
METHODS = ["M1 standard 20 m", "M2 10 m sharpened", "M3 SWiFT-Bank"]
MAJULI_TILE = "dw_94p4985339347_27p0498068352-20190119"
DISTRICTS = {
    "Dibrugarh": {"repo": "sudipta2020-1/dibrugarh-erosion-monitor", "aoi": "data/dibrugarh_boundary.geojson"},
    "Majuli": {"repo": "sudipta2020-1/majuli-erosion-monitor", "aoi": "data/majuli_boundary.geojson"},
}


def _get(url, path=None, timeout=600):
    for k in range(4):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 404:
                raise FileNotFoundError(url)
            r.raise_for_status()
            if path:
                Path(path).write_bytes(r.content)
                return path
            return r.content
        except FileNotFoundError:
            raise
        except Exception as e:
            print("  retry", k + 1, url, e)
            time.sleep(10 * (k + 1))
    raise RuntimeError("download failed: " + url)


# ------------------------------------------------------------------ test 1
def _block(a, k):
    """Mean over k x k blocks (cropped to a multiple of k)."""
    h, w = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    return a[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def _counts(pred, ref, mask):
    p, r = pred[mask], ref[mask]
    return np.array([np.sum(p & r), np.sum(p & ~r), np.sum(~p & r), np.sum(~p & ~r)], "int64")  # TP FP FN TN


def _scores(c):
    tp, fp, fn, tn = [float(v) for v in c]
    n = tp + fp + fn + tn
    return {"overall_accuracy": (tp + tn) / n if n else np.nan,
            "water_f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else np.nan,
            "water_iou": tp / (tp + fp + fn) if tp + fp + fn else np.nan,
            "water_users": tp / (tp + fp) if tp + fp else np.nan,
            "water_producers": tp / (tp + fn) if tp + fn else np.nan, "pixels": int(n)}


def _load_scene(row, lab_da):
    tile = str(row["s2_l2a_system_index"]).split("_")[-1].lstrip("T")
    day = pd.to_datetime(float(row["s2_l2a_system_time_start"]), unit="ms").strftime("%Y-%m-%d")
    b = lab_da.rio.transform_bounds("EPSG:4326")
    items = _catalog().search(collections=["sentinel-2-l2a"], bbox=list(b), datetime=f"{day}/{day}",
                              query={"s2:mgrs_tile": {"eq": tile}}).item_collection()
    items = list(items)
    if not items:
        return None, day, tile
    items.sort(key=lambda i: i.properties.get("s2:processing_baseline", ""))
    it = items[-1]
    from odc.geo.geobox import GeoBox
    gb = GeoBox(lab_da.shape, lab_da.rio.transform(), lab_da.rio.crs.to_wkt())
    ds = stac_load([it], bands=["B03", "B08", "B11", "SCL"], geobox=gb, patch_url=SIGN,
                   resampling={"B11": "bilinear", "*": "nearest"}, fail_on_error=False).isel(time=0)
    off = np.float32(_s2_offset(it))
    clear = np.isin(ds["SCL"].values, S2_CLEAR_SCL)
    refl = {}
    for bnd in ["B03", "B08", "B11"]:
        v = ds[bnd].values.astype("float32")
        refl[bnd] = np.where((v > 0) & clear, np.clip((v + off) / 1e4, 0, 1), np.nan).astype("float32")
    return refl, day, tile


def _sharpen(g, n, s):
    n20 = hi._nir20(n[None])[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.clip(s * n / n20, 0, 1)


def _mndwi(g, s):
    with np.errstate(invalid="ignore", divide="ignore"):
        return (g - s) / (g + s)


def _hard(m):
    t, _ = an.edge_otsu(m, 0.0, True, bounds=[-0.3, 0.3], min_px=500)
    return m > t, t


def test1():
    WORK.mkdir(parents=True, exist_ok=True)
    zpath = WORK / "dw_tiles.zip"
    if not zpath.exists():
        _get(DW_URL, zpath)
    z = zipfile.ZipFile(zpath)
    meta = pd.read_csv(io.BytesIO(z.read("dw_test/meta.csv")))
    print(f"Dynamic World expert tiles: {len(meta)}")
    tiles, sub = [], []
    pooled = {m: np.zeros(4, "int64") for m in METHODS}
    edge = {m: np.zeros(4, "int64") for m in METHODS}
    t0 = time.time()
    for i, row in meta.iterrows():
        name = row["filename"]
        tif = WORK / (name + ".tif")
        if not tif.exists():
            tif.write_bytes(z.read(f"dw_test/{name}.tif"))
        lab_da = rioxarray.open_rasterio(tif).squeeze("band", drop=True)
        lab = lab_da.values
        labelled = lab > 0
        water = lab == 1
        nl, nw = int(labelled.sum()), int((water & labelled).sum())
        if nl == 0 or nw < 0.01 * nl or (nl - nw) < 0.01 * nl:
            continue
        try:
            refl, day, tile = _load_scene(row, lab_da)
        except Exception as e:
            print(f"  {name}: scene not read ({e})")
            continue
        if refl is None:
            print(f"  {name}: no matching scene")
            continue
        g, n, s10 = refl["B03"], refl["B08"], refl["B11"]
        ok = labelled & np.isfinite(g) & np.isfinite(n) & np.isfinite(s10)
        ok[(ok.shape[0] // 2) * 2:, :] = False         # last row and column have no 20 m block
        ok[:, (ok.shape[1] // 2) * 2:] = False
        if ok.sum() < 5000:
            continue
        # M1: 20 m MNDWI, edge Otsu, back to 10 m
        g20, s20 = _block(g, 2), _block(s10, 2)
        w20, t1 = _hard(_mndwi(g20, s20))
        p1 = np.zeros_like(water)
        r20 = np.repeat(np.repeat(w20, 2, 0), 2, 1)
        p1[:r20.shape[0], :r20.shape[1]] = r20
        # M2: 10 m sharpened MNDWI, edge Otsu
        s_sh = _sharpen(g, n, s10)
        p2, t2 = _hard(_mndwi(g, s_sh))
        # M3: SWiFT-Bank water fraction
        f3, _ = sw.water_fraction(g, n, s_sh)
        p3 = np.nan_to_num(f3, nan=0) >= 0.5
        ok &= np.isfinite(f3)
        shore = ndimage.binary_dilation(water, iterations=2) & ~ndimage.binary_erosion(water, iterations=2) & ok
        import re
        mm = re.match("dw_(-?[0-9p]+)_(-?[0-9p]+)-", name)
        rec = {"tile": name, "date": day, "mgrs": tile, "lon": float(mm.group(1).replace("p", ".")),
               "lat": float(mm.group(2).replace("p", ".")),
               "labelled_px": int(ok.sum()), "water_share": round(float(water[ok].mean()), 3),
               "t_m1": round(t1, 3), "t_m2": round(t2, 3)}
        for m, p in zip(METHODS, [p1, p2, p3]):
            c, ce = _counts(p, water, ok), _counts(p, water, shore)
            pooled[m] += c
            edge[m] += ce
            sc, se = _scores(c), _scores(ce)
            key = m.split()[0]
            rec[f"{key}_oa"], rec[f"{key}_f1"] = round(sc["overall_accuracy"], 4), round(sc["water_f1"], 4)
            rec[f"{key}_shore_oa"] = round(se["overall_accuracy"], 4) if se["pixels"] else np.nan
            for j, v in enumerate(c):
                rec[f"{key}_c{j}"] = int(v)
            for j, v in enumerate(ce):
                rec[f"{key}_e{j}"] = int(v)
        # Sub-pixel test at 20 m and 30 m support
        for k in (2, 3):
            okb = _block(ok.astype("float32"), k) == 1
            truth = _block(water.astype("float32"), k)
            gk, nk, sk = _block(g, k), _block(n, k), _block(s10, k)
            old = (sw.WIN_SMALL, sw.WIN_LARGE)
            sw.WIN_SMALL, sw.WIN_LARGE = max(7, 31 // k | 1), max(21, 151 // k | 1)
            fk, _ = sw.water_fraction(gk, nk, sk)
            sw.WIN_SMALL, sw.WIN_LARGE = old
            hk, _ = _hard(_mndwi(gk, sk))
            okb &= np.isfinite(fk)
            mixed = okb & (truth > 0) & (truth < 1)
            if mixed.sum() < 20:
                continue
            sub.append({"tile": name, "support_m": 10 * k, "mixed_blocks": int(mixed.sum()),
                        "true_water": float(truth[mixed].sum()),
                        "swift_abs_err": float(np.abs(fk[mixed] - truth[mixed]).sum()),
                        "hard_abs_err": float(np.abs(hk[mixed].astype(float) - truth[mixed]).sum()),
                        "swift_sum": float(fk[mixed].sum()), "hard_sum": float(hk[mixed].sum())})
        tiles.append(rec)
        if len(tiles) % 10 == 0:
            print(f"  {len(tiles)} tiles done ({(time.time() - t0) / 60:.1f} min)", flush=True)

    T = pd.DataFrame(tiles)
    S = pd.DataFrame(sub)
    T.to_csv(OUT / "dw_tiles.csv", index=False)
    S.to_csv(OUT / "dw_subpixel.csv", index=False)
    res = {"tiles_used": int(len(T)), "tiles_in_set": int(len(meta)),
           "pooled": {m: _scores(pooled[m]) for m in METHODS},
           "shoreline_20m": {m: _scores(edge[m]) for m in METHODS}}

    # Bootstrap over tiles for pooled scores and the differences to SWiFT-Bank
    rng = np.random.default_rng(1)
    keys = [m.split()[0] for m in METHODS]
    B = 1000
    boot = {f"{k}_{what}": [] for k in keys for what in ("oa", "f1", "shore_oa")}
    for _ in range(B):
        idx = rng.integers(0, len(T), len(T))
        Tb = T.iloc[idx]
        for k in keys:
            c = Tb[[f"{k}_c{j}" for j in range(4)]].sum().values
            ce = Tb[[f"{k}_e{j}" for j in range(4)]].sum().values
            sc, se = _scores(c), _scores(ce)
            boot[f"{k}_oa"].append(sc["overall_accuracy"])
            boot[f"{k}_f1"].append(sc["water_f1"])
            boot[f"{k}_shore_oa"].append(se["overall_accuracy"])
    ci = lambda a: [round(float(np.nanpercentile(a, 2.5)), 4), round(float(np.nanpercentile(a, 97.5)), 4)]
    res["ci95"] = {k: ci(v) for k, v in boot.items()}
    diffs = {}
    for k in keys[:2]:
        for what in ("oa", "f1", "shore_oa"):
            d = np.array(boot[f"M3_{what}"]) - np.array(boot[f"{k}_{what}"])
            diffs[f"M3_minus_{k}_{what}"] = {"mean": round(float(np.nanmean(d)), 4), "ci95": ci(d),
                                             "share_positive": round(float(np.mean(d > 0)), 3)}
    res["differences"] = diffs
    if len(S):
        subres = {}
        for kk, Sg in S.groupby("support_m"):
            nb = Sg["mixed_blocks"].sum()
            tw = Sg["true_water"].sum()
            subres[str(kk)] = {"mixed_blocks": int(nb), "tiles": int(len(Sg)),
                               "swift_mae": round(float(Sg["swift_abs_err"].sum() / nb), 4),
                               "hard_mae": round(float(Sg["hard_abs_err"].sum() / nb), 4),
                               "swift_area_error_pct": round(float(100 * (Sg["swift_sum"].sum() - tw) / tw), 1),
                               "hard_area_error_pct": round(float(100 * (Sg["hard_sum"].sum() - tw) / tw), 1)}
        res["subpixel"] = subres
    mj = T[T["tile"] == MAJULI_TILE]
    if len(mj):
        r = mj.iloc[0]
        res["majuli_tile"] = {"tile": MAJULI_TILE, "date": r["date"],
                              **{m: {"overall_accuracy": r[f"{m.split()[0]}_oa"], "water_f1": r[f"{m.split()[0]}_f1"],
                                     "shoreline_oa": r[f"{m.split()[0]}_shore_oa"]} for m in METHODS}}
    print(json.dumps(res, indent=1, default=float))
    return res


# ------------------------------------------------------------------ test 2
def test2():
    import geopandas as gpd
    for ext in ["shp", "shx", "dbf", "prj"]:
        p = WORK / f"REAL_reach.{ext}"
        if not p.exists():
            _get(REAL_URL.format(f"REAL_reach.{ext}"), p, timeout=1800)
    aois = []
    for d, v in DISTRICTS.items():
        a = gpd.read_file(io.BytesIO(_get(f"https://raw.githubusercontent.com/{v['repo']}/main/{v['aoi']}")))
        a = a.to_crs(4326).dissolve()
        a["district"] = d
        aois.append(a[["district", "geometry"]])
    A = pd.concat(aois, ignore_index=True)
    bbox = tuple(np.array(A.total_bounds) + np.array([-0.05, -0.05, 0.05, 0.05]))
    # The reach file stores SWORD reach centre longitude and latitude as x and y
    R = gpd.read_file(WORK / "REAL_reach.shp",
                      where=f"x >= {bbox[0]} AND x <= {bbox[2]} AND y >= {bbox[1]} AND y <= {bbox[3]}")
    print("REAL columns:", list(R.columns))
    print(f"  {len(R)} REAL reaches in the box")
    col = "ERate" if "ERate" in R.columns else next(c for c in R.columns if "ERate" in c or "Erate" in c)
    crs = "EPSG:32646"
    R = R.to_crs(crs)
    A = A.to_crs(crs)
    R = gpd.sjoin(R, A, predicate="intersects", how="inner").drop(columns="index_right")
    R = R[~R.index.duplicated()]
    R["length_m"] = R.geometry.length
    R = R[(R[col] > 0) & (R["length_m"] > 2000)].copy()
    R["rid"] = np.arange(len(R))
    print(f"  {len(R)} reaches with a valid erosion rate in the two districts")
    yrs = {}
    pts = {m: [] for m in METHODS}
    for d, v in DISTRICTS.items():
        base = f"https://raw.githubusercontent.com/{v['repo']}/main/docs/data/"
        summ = json.loads(_get(base + "summary.json"))
        yrs[d] = float(summ.get("years_between_periods") or 6.0)
        # Standard 20 m: bank erosion and char loss pixels
        tif = WORK / f"{d}_change_class.tif"
        _get(f"https://github.com/{v['repo']}/releases/latest/download/change_class.tif", tif)
        c = rioxarray.open_rasterio(tif).squeeze("band", drop=True)
        rr, cc = np.nonzero(np.isin(c.values, [1, 5]))
        tr = c.rio.transform()
        xs, ys = tr * (cc + 0.5, rr + 0.5)
        px = abs(tr.a * tr.e)
        pts[METHODS[0]].append(gpd.GeoDataFrame({"area_m2": np.full(len(xs), px), "district": d},
                                                geometry=gpd.points_from_xy(xs, ys), crs=c.rio.crs).to_crs(crs))
        for m, f, fld in [(METHODS[1], "hires/loss_patches_10m.geojson", "area_ha"),
                          (METHODS[2], "swift/loss_patches.geojson", "loss_ha")]:
            try:
                g = gpd.read_file(io.BytesIO(_get(base + f)))
                g = g.to_crs(crs)
                g = gpd.GeoDataFrame({"area_m2": g[fld].astype(float) * 1e4, "district": d},
                                     geometry=g.geometry.representative_point(), crs=crs)
                pts[m].append(g)
            except Exception as e:
                print(f"  {d} {m}: not available ({e})")
    out = R[["rid", "district", col, "length_m", "geometry"]].copy()
    corr = {}
    for m in METHODS:
        if not pts[m]:
            continue
        P = pd.concat(pts[m], ignore_index=True)
        J = gpd.sjoin_nearest(P, R[["rid", "geometry"]], max_distance=3000, how="inner")
        area = J.groupby("rid")["area_m2"].sum()
        key = m.split()[0]
        out[key + "_area_ha"] = out["rid"].map(area).fillna(0) / 1e4
        dy = out["district"].map(yrs)
        out[key + "_rate"] = out[key + "_area_ha"] * 1e4 / (2 * out["length_m"]) / dy
        have = out[out["district"].isin(set(P["district"]))]
        rho, p = stats.spearmanr(have[col], have[key + "_rate"])
        corr[m] = {"spearman_rho": round(float(rho), 3), "p_value": float(p), "reaches": int(len(have)),
                   "median_rate_m_yr": round(float(have[key + "_rate"].median()), 2)}
    res = {"reaches": int(len(out)), "real_field": col, "real_median_rate_m_yr": round(float(out[col].median()), 2),
           "real_years": [int(R["bestYear1"].median()), int(R["bestYear2"].median())] if "bestYear1" in R else None,
           "correlation": corr}
    o = out.to_crs(4326)
    o["geometry"] = o.geometry.simplify(0.0005)
    o.to_file(OUT / "real_reaches.geojson", driver="GeoJSON")
    print(json.dumps(res, indent=1))
    return res


def main():
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    f = OUT / "summary.json"
    res = json.loads(f.read_text()) if f.exists() else {}
    res["run"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    if which in ("all", "test2"):
        try:
            res["test2_real"] = test2()
        except Exception as e:
            import traceback
            traceback.print_exc()
            res["test2_real"] = {"error": str(e)}
        f.write_text(json.dumps(res, indent=2, default=float))
    if which in ("all", "test1"):
        res["test1_dynamic_world"] = test1()
    res["runtime_min"] = round((time.time() - t0) / 60, 1)
    f.write_text(json.dumps(res, indent=2, default=float))


if __name__ == "__main__":
    main()
