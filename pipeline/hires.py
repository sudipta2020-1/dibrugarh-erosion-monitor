"""
High-resolution comparison run: 10 m water mapping with a sharpened SWIR band
and sub-pixel bank lines, compared with the standard 20 m method.

Method
  1. Sentinel-2 L2A at 10 m for the same two windows as the latest standard run.
     The 20 m SWIR band (B11) is sharpened to 10 m with the 10 m NIR band
     (ratio modulation: B11 x B08 / B08 smoothed to 20 m), so MNDWI is computed
     at 10 m (after Du et al., 2016).
  2. Every clear date is classified as water or land at a set of thresholds; the
     threshold is chosen by edge-based Otsu (Donchyts et al., 2016) and a pixel is
     water if it was water on at least half of its clear dates (as in the 20 m run).
  3. Land-to-water change is mapped at 10 m with a minimum patch of 5 pixels
     (0.05 ha), compared with 10 pixels at 20 m (0.4 ha) in the standard run.
  4. Bank lines are traced at sub-pixel precision as the 0.5 contour of the water
     frequency surface with marching squares (Bishop-Taylor et al., 2019, 2021),
     and bank retreat is measured between the baseline and current bank lines.
  5. The result is compared with the 20 m change map of the latest release.

Only the river corridor is processed: land within 1 km of the river channels
(water bodies of 50 ha or more that held water at any time in the JRC record
1984-2021). Bank erosion happens there, and it keeps the 10 m run within the
time and memory limits of a free GitHub runner.

Run:  python pipeline/hires.py            (periods from docs/data/summary.json)
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import requests
import rioxarray  # noqa: F401
import xarray as xr
import yaml
from scipy import ndimage

import analysis as an
from acquire import (CHUNKS, OPT_THRESHOLDS, S2_CLEAR_SCL, SIGN, _clearest, _compute_in_strips,
                     _s2_offset, _search, get_aoi, stac_load)

RES = 10
MMU_PX = 5                       # 0.05 ha at 10 m
MMU20_PX = 10                    # 0.4 ha at 20 m (standard run)
SMALL_HA = 0.4
COUNT_BANDS = ["n_clear"] + [f"w_{t:+.2f}" for t in OPT_THRESHOLDS]
SIZE_BINS = [0, 0.05, 0.1, 0.2, 0.4, 1, 5, 20, 1e9]
SIZE_LABELS = ["< 0.05", "0.05–0.1", "0.1–0.2", "0.2–0.4", "0.4–1", "1–5", "5–20", "> 20"]
CLASSES = {1: "Bank erosion (stable land to water)", 2: "Accretion (water to land)",
           5: "Char or sandbar lost (within river belt)", 6: "New inland water (pond or flooding)"}
COLORS = {1: "#d7191c", 5: "#f59ec0", 6: "#66c2a5", 2: "#2c7bb6"}


# ----------------------------------------------------------------- corridor
def corridor(cfg, aoi, buffer_m=1000, min_channel_ha=50):
    """River corridor: river channels (JRC water at any time 1984-2021, in water bodies
    of at least min_channel_ha), widened by buffer_m, inside the district."""
    from rasterio.features import geometry_mask
    jrc = rioxarray.open_rasterio(cfg["history_raster"]).squeeze("band", drop=True)
    jrc = jrc.rio.reproject(cfg["crs"], resolution=100)
    ever = ((jrc.values >= 1) & (jrc.values <= 10))
    lab, _ = ndimage.label(ever, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    ever = np.isin(lab, np.nonzero(sizes >= min_channel_ha)[0])     # 100 m cells = 1 ha
    grow = ndimage.distance_transform_edt(~ever) * 100 <= buffer_m
    a = aoi.to_crs(cfg["crs"])
    inside = ~geometry_mask(a.geometry, out_shape=grow.shape, transform=jrc.rio.transform())
    m = grow & inside
    rows, cols = np.nonzero(m)
    tr = jrc.rio.transform()
    x0, y0 = tr * (cols.min(), rows.max() + 1)
    x1, y1 = tr * (cols.max() + 1, rows.min())
    mask = jrc.copy(data=m.astype("uint8"))
    print(f"  corridor: {m.sum() * 0.01:.0f} km2 of {inside.sum() * 0.01:.0f} km2 district")
    return mask, (x0, y0, x1, y1)


def to_ll(bounds, crs):
    from pyproj import Transformer
    t = Transformer.from_crs(crs, 4326, always_xy=True)
    w, s = t.transform(bounds[0], bounds[1])
    e, n = t.transform(bounds[2], bounds[3])
    return [w, s, e, n]


# -------------------------------------------------------------- acquisition
def s2_10m(bbox_ll, period, crs, max_cloud, per_tile):
    """Per-date water counts and median MNDWI at 10 m with a sharpened SWIR band."""
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
    nir20 = nir.rolling(x=2, y=2, center=True, min_periods=1).mean()
    swir = (refl["B11"] * (nir / nir20)).clip(0, 1)            # SWIR sharpened to 10 m
    mndwi = (refl["B03"] - swir) / (refl["B03"] + swir)
    ok = np.isfinite(mndwi)
    out = [ok.sum("time").astype("uint8").rename("n_clear"),
           mndwi.median("time", skipna=True).astype("float32").rename("mndwi")]
    for t, name in zip(OPT_THRESHOLDS, COUNT_BANDS[1:]):
        out.append((ok & (mndwi > t)).sum("time").astype("uint8").rename(name))
    res = _compute_in_strips(xr.merge(out), f"Sentinel-2 10 m {period[0][:7]}")
    return res.rio.write_crs(crs)


# ----------------------------------------------------------------- analysis
def water_state(ds, cc, inside):
    t, n_edge = an.edge_otsu(np.where(inside, ds["mndwi"].values, np.nan), cc["mndwi_water"], True,
                             bounds=cc.get("mndwi_bounds", [-0.3, 0.3]))
    i = int(np.argmin([abs(v - t) for v in OPT_THRESHOLDS]))
    n = ds["n_clear"].values.astype("float32")
    k = ds[COUNT_BANDS[1 + i]].values.astype("float32")
    with np.errstate(invalid="ignore", divide="ignore"):
        f = np.where(n >= cc.get("min_observations", 2), k / n, np.nan)
    # Pixels without enough clear dates fall back to the median composite
    f = np.where(np.isfinite(f), f, (ds["mndwi"].values > OPT_THRESHOLDS[i]).astype("float32"))
    water = f >= cc.get("water_frequency_min", 0.5)
    conf = an.label_confidence(k, n)
    return {"freq": f, "water": water, "conf": conf, "threshold": OPT_THRESHOLDS[i],
            "otsu": round(t, 3), "edge_px": n_edge}


def change_map(wb, wc, jrc, inside, tmpl):
    c = np.zeros(wb.shape, "uint8")
    c[~wb & wc] = 1
    c[wb & ~wc] = 2
    if jrc is not None:
        c[(c == 1) & (jrc >= 1) & (jrc <= 10)] = 5
    c[~inside] = 0
    chg = tmpl.copy(data=c)
    chg = an.split_inland_water(chg, tmpl.copy(data=wb), RES, 50.0, touch_px=4)
    return chg


def sieve(arr, min_px):
    from rasterio.features import sieve as _sieve
    return _sieve(arr.astype("int32"), size=min_px, connectivity=8).astype("uint8")


def bank_lines(freq, river, transform, min_len_m=300):
    """Sub-pixel bank lines: 0.5 contour of the smoothed water-frequency surface
    (marching squares with linear interpolation), kept where they follow a river channel."""
    from shapely.geometry import LineString
    from skimage.measure import find_contours
    f = ndimage.gaussian_filter(np.nan_to_num(freq, nan=0.0), 0.7)
    edge = ndimage.binary_dilation(river, iterations=3) & ~ndimage.binary_erosion(river, iterations=3)
    lines = []
    for c in find_contours(f, 0.5):
        if len(c) < 4:
            continue
        r, col = c[:, 0], c[:, 1]
        near = edge[np.clip(np.round(r).astype(int), 0, edge.shape[0] - 1),
                    np.clip(np.round(col).astype(int), 0, edge.shape[1] - 1)]
        if near.mean() < 0.5:
            continue
        xs = transform.c + (col + 0.5) * transform.a
        ys = transform.f + (r + 0.5) * transform.e
        ln = LineString(np.column_stack([xs, ys]))
        if ln.length >= min_len_m:
            lines.append(ln)
    return lines


def subpixel_retreat(patches_gdf, base_lines, cur_lines):
    """Bank retreat of each loss patch, measured between the sub-pixel bank lines:
    the largest distance from current bank-line points inside the patch to the baseline line."""
    from shapely import STRtree
    from shapely.geometry import MultiPoint
    if not base_lines or not cur_lines:
        return np.full(len(patches_gdf), np.nan)
    bt, ct = STRtree(base_lines), STRtree(cur_lines)
    out = []
    for g in patches_gdf.geometry:
        zone = g.buffer(15)
        pts = []
        for j in ct.query(zone):
            seg = cur_lines[j].intersection(zone)
            for part in getattr(seg, "geoms", [seg]):
                if not part.is_empty and part.geom_type in ("LineString", "LinearRing", "Point"):
                    pts += list(part.coords)
        if not pts:
            out.append(np.nan)
            continue
        mp = MultiPoint(pts)
        near = [base_lines[j] for j in bt.query(g.buffer(500))]
        if not near:
            out.append(np.nan)
            continue
        from shapely.ops import unary_union
        bl = unary_union(near)
        out.append(max(bl.distance(p) for p in mp.geoms))
    return np.round(np.array(out, float), 1)


def patch_table(chg, classes, min_px):
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape
    arr = np.where(np.isin(chg.values, classes), 1, 0).astype("uint8")
    arr = sieve(arr, min_px)
    recs = [{"geometry": shape(g), "area_ha": shape(g).area / 1e4}
            for g, v in shapes(arr, mask=arr > 0, transform=chg.rio.transform(), connectivity=8) if v == 1]
    if not recs:
        return gpd.GeoDataFrame({"area_ha": []}, geometry=[], crs=chg.rio.crs), arr
    return gpd.GeoDataFrame(recs, geometry="geometry", crs=chg.rio.crs), arr


def hist(areas):
    h, _ = np.histogram(np.asarray(areas, float), bins=SIZE_BINS)
    return [int(v) for v in h]


# ------------------------------------------------------------------ outputs
def png(da, colors, path, max_width=6000):
    from PIL import Image
    from pyproj import Transformer
    from rasterio.enums import Resampling
    w = da.sizes["x"]
    res = abs(float(da.x[1] - da.x[0])) * max(1.0, w / max_width)
    m = da.astype("float32").rio.write_nodata(0).rio.reproject("EPSG:3857", resolution=res,
                                                                 resampling=Resampling.nearest)
    v = np.nan_to_num(m.values, nan=0).astype(int)
    rgba = np.zeros(v.shape + (4,), "uint8")
    for k, c in colors.items():
        c = c.lstrip("#")
        rgba[v == k] = [int(c[i:i + 2], 16) for i in (0, 2, 4)] + [235]
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
    x0, y0, x1, y1 = m.rio.bounds()
    t = Transformer.from_crs(3857, 4326, always_xy=True)
    (w_, s_), (e_, n_) = t.transform(x0, y0), t.transform(x1, y1)
    return [[round(s_, 6), round(w_, 6)], [round(n_, 6), round(e_, 6)]]


def lines_geojson(lines, crs, path, tol=3.0):
    import geopandas as gpd
    g = gpd.GeoDataFrame(geometry=[l.simplify(tol) for l in lines], crs=crs).to_crs(4326)
    g["geometry"] = g.geometry.set_precision(1e-6)
    path.write_text(g.to_json(drop_id=True))
    return round(sum(l.length for l in lines) / 1000, 1)


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    t_start = time.time()
    cfg = yaml.safe_load(Path(args.config).read_text())
    cc = cfg["change"]
    pub = Path(cfg["publish_dir"]) / "hires"
    pub.mkdir(parents=True, exist_ok=True)
    summ = json.loads((Path(cfg["publish_dir"]) / "summary.json").read_text())
    periods = {"baseline": summ["period_baseline"], "current": summ["period_current"]}
    print("Periods:", periods)

    aoi, _ = get_aoi(cfg)
    cmask, bounds = corridor(cfg, aoi)
    bbox_ll = to_ll(bounds, cfg["crs"])

    raw = Path(cfg["output_dir"]) / cfg["project_name"] / "hires"
    raw.mkdir(parents=True, exist_ok=True)
    st = {}
    for tag, per in periods.items():
        f = raw / f"s2_10m_{tag}_{per[1]}.nc"
        if f.exists():
            ds = xr.open_dataset(f).load().rio.write_crs(cfg["crs"])
        else:
            ds = s2_10m(bbox_ll, per, cfg["crs"], cfg["max_cloud_cover"], cfg.get("max_scenes_per_tile", 15))
            ds.to_netcdf(f)
        st[tag] = ds
    tmpl = st["current"]["n_clear"].copy(data=np.zeros(st["current"]["n_clear"].shape, "uint8"))
    # District and corridor masks on the 10 m grid
    from rasterio.features import geometry_mask
    inside = ~geometry_mask(aoi.to_crs(cfg["crs"]).geometry, out_shape=tmpl.shape, transform=tmpl.rio.transform())
    from rasterio.enums import Resampling
    corr = cmask.rio.reproject_match(tmpl, resampling=Resampling.nearest).values.astype(bool)
    inside &= corr
    print(f"  10 m grid {tmpl.shape[1]} x {tmpl.shape[0]}; {inside.sum() * 1e-4:.0f} km2 analysed")

    ws = {k: water_state(st[k], cc, inside) for k in st}
    jrc = an.jrc_transitions(tmpl, cfg.get("history_raster"))
    chg = change_map(ws["baseline"]["water"], ws["current"]["water"], jrc, inside, tmpl)
    c = chg.values.copy()
    for k in CLASSES:                                    # remove patches below 0.05 ha
        keep = sieve((c == k).astype("uint8"), MMU_PX) == 1
        c[(c == k) & ~keep] = 0
    chg = chg.copy(data=c)
    conf = np.where(np.isin(c, [1, 2, 5, 6]), ws["baseline"]["conf"] * ws["current"]["conf"], np.nan)

    # Sub-pixel bank lines and retreat
    river = an.river_mask(ws["baseline"]["water"] & inside, RES, 50.0)
    river_c = an.river_mask(ws["current"]["water"] & inside, RES, 50.0)
    tr = tmpl.rio.transform()
    lb = bank_lines(ws["baseline"]["freq"], river, tr)
    lc = bank_lines(ws["current"]["freq"], river_c, tr)
    print(f"  bank lines: {len(lb)} baseline, {len(lc)} current segments")

    loss10, arr10 = patch_table(chg, [1, 5], MMU_PX)
    years = (np.datetime64(periods["current"][1]) - np.datetime64(periods["baseline"][1])).astype(int) / 365.25
    if len(loss10):
        loss10["retreat_subpixel_m"] = subpixel_retreat(loss10, lb, lc)
        d = ndimage.distance_transform_edt(~ws["baseline"]["water"]) * RES
        from rasterio.features import rasterize
        ids = rasterize(((g, i + 1) for i, g in enumerate(loss10.geometry)), out_shape=tmpl.shape, transform=tr, fill=0, dtype="int32")
        loss10["retreat_pixel_m"] = np.round(ndimage.maximum(d, ids, np.arange(1, len(loss10) + 1)), 0)
        loss10["class"] = [CLASSES[int(np.bincount(chg.values[ids == i + 1]).argmax())] if (ids == i + 1).any() else ""
                           for i in range(len(loss10))]

    # Standard 20 m result from the latest release, on the same grid
    repo = summ.get("repository", "")
    std = {}
    try:
        url = f"https://github.com/{repo}/releases/latest/download/change_class.tif"
        p20 = raw / "change_class_20m.tif"
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        p20.write_bytes(r.content)
        c20_native = rioxarray.open_rasterio(p20).squeeze("band", drop=True)
        c20 = c20_native.rio.reproject_match(tmpl, resampling=Resampling.nearest).values
        c20 = np.where(inside, np.nan_to_num(c20, nan=0), 0).astype("uint8")
        # Bank loss as the standard run reports it: patches of at least 10 pixels (0.4 ha)
        rep20 = sieve(np.isin(c20_native.values, [1, 5]).astype("uint8"), MMU20_PX)
        rep20 = c20_native.copy(data=rep20).rio.reproject_match(tmpl, resampling=Resampling.nearest).values
        std = {"ok": True, "c20": c20, "native": c20_native, "rep20": np.where(inside, rep20 == 1, False)}
    except Exception as e:
        print("  could not read the 20 m release:", e)

    px_ha = RES * RES / 1e4
    area10 = {CLASSES[k]: round(float((c == k).sum() * px_ha), 1) for k in CLASSES}
    out = {"run": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "periods": periods,
           "district": cfg.get("aoi_district"), "resolution_m": RES, "min_patch_ha": MMU_PX * px_ha,
           "area_analysed_km2": round(float(inside.sum() * px_ha / 100), 1),
           "years_between": round(float(years), 2),
           "water": {k: {"threshold": ws[k]["threshold"], "otsu": ws[k]["otsu"], "edge_px": ws[k]["edge_px"]} for k in ws},
           "hires": {"area_ha": area10,
                     "bank_loss_patches": int(len(loss10)),
                     "bank_loss_ha": round(float(loss10.area_ha.sum()) if len(loss10) else 0.0, 1),
                     "size_hist": hist(loss10.area_ha if len(loss10) else []),
                     "small_patches": int((loss10.area_ha < SMALL_HA).sum()) if len(loss10) else 0,
                     "small_ha": round(float(loss10.area_ha[loss10.area_ha < SMALL_HA].sum()), 2) if len(loss10) else 0.0,
                     "mean_confidence": round(float(np.nanmean(conf)), 3) if np.isfinite(conf).any() else None,
                     "bankline_km": {}},
           "size_labels": SIZE_LABELS}
    if len(loss10):
        rs, rp = loss10["retreat_subpixel_m"], loss10["retreat_pixel_m"]
        out["hires"]["retreat"] = {"median_subpixel_m": round(float(np.nanmedian(rs)), 1),
                                   "median_pixel_m": round(float(np.nanmedian(rp)), 1),
                                   "p90_subpixel_m": round(float(np.nanpercentile(rs, 90)), 1) if np.isfinite(rs).any() else None}

    if std:
        c20 = std["c20"]
        area20 = {CLASSES[k]: round(float((c20 == k).sum() * px_ha), 1) for k in CLASSES}
        loss20_10 = std["rep20"]
        loss10_m = np.isin(c, [1, 5])
        inter, union = (loss20_10 & loss10_m).sum(), (loss20_10 | loss10_m).sum()
        tol20 = ndimage.binary_dilation(loss20_10, iterations=2)
        tol10 = ndimage.binary_dilation(loss10_m, iterations=2)
        n20 = std["native"]
        p20, _ = patch_table(n20, [1, 5], MMU20_PX)         # as reported by the standard run (0.4 ha)
        # 20 m patches inside the analysed corridor only
        if len(p20):
            from shapely.geometry import box
            p20 = p20[p20.intersects(box(*bounds))]
        if len(loss10):
            in20 = [bool(loss20_10[ids == i + 1].any()) for i in range(len(loss10))]
            loss10["also_in_20m"] = in20
            new_small = loss10[(~loss10["also_in_20m"]) & (loss10.area_ha < SMALL_HA)]
        else:
            new_small = loss10
        out["standard"] = {"area_ha": area20, "resolution_m": 20, "min_patch_ha": 0.4,
                           "bank_loss_patches": int(len(p20)), "bank_loss_ha": round(float(p20.area_ha.sum()) if len(p20) else 0.0, 1),
                           "size_hist": hist(p20.area_ha if len(p20) else [])}
        out["agreement"] = {"iou_bank_loss": round(float(inter / union), 3) if union else None,
                            "share_10m_within_20m_of_standard": round(float((loss10_m & tol20).sum() / max(loss10_m.sum(), 1)), 3),
                            "share_standard_within_20m_of_10m": round(float((loss20_10 & tol10).sum() / max(loss20_10.sum(), 1)), 3),
                            "new_small_patches": int(len(new_small)),
                            "new_small_ha": round(float(new_small.area_ha.sum()) if len(new_small) else 0.0, 2)}
        out["layers"] = {"change20": {"file": "hires/change20.png",
                                      "bounds": png(xr.DataArray(c20, coords=tmpl.coords, dims=tmpl.dims).rio.write_crs(cfg["crs"]),
                                                    COLORS, pub / "change20.png")}}
    else:
        out["layers"] = {}
    out["layers"]["change10"] = {"file": "hires/change10.png", "bounds": png(chg, COLORS, pub / "change10.png")}
    out["legend"] = [[CLASSES[k], COLORS[k]] for k in [1, 5, 6, 2]]
    out["hires"]["bankline_km"] = {"baseline": lines_geojson(lb, cfg["crs"], pub / "banklines_baseline.geojson"),
                                   "current": lines_geojson(lc, cfg["crs"], pub / "banklines_current.geojson")}
    if len(loss10):
        g = loss10.copy()
        g["geometry"] = g.geometry.simplify(2)
        g = g.to_crs(4326)
        g["area_ha"] = g["area_ha"].round(3)
        g["small"] = g["area_ha"] < SMALL_HA
        cent = g.geometry.representative_point()
        g["lat"], g["lon"] = cent.y.round(6), cent.x.round(6)
        g["geometry"] = g.geometry.set_precision(1e-6)
        (pub / "loss_patches_10m.geojson").write_text(g.to_json(drop_id=True))
        only10 = ~g["also_in_20m"].astype(bool) if "also_in_20m" in g else True
        out["examples_small"] = g[g["small"] & only10].nlargest(8, "area_ha")[["lat", "lon", "area_ha"]].to_dict("records")
    out["runtime_min"] = round((time.time() - t_start) / 60, 1)
    (pub / "summary.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k not in ("layers",)}, indent=2))


if __name__ == "__main__":
    main()
