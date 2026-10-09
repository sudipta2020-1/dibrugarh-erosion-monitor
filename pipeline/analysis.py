"""
Erosion analysis on the acquired layers.

1. Spectral indices  : NDVI, MNDWI, BSI (bare soil index)
2. RUSLE soil loss   : A = R * K * LS * C * P   (t/ha/yr)
3. Risk classes      : 1 very low ... 5 very high
4. Water mapping     : water frequency from every clear date, with thresholds
                       set by edge-based Otsu (Donchyts et al., 2016)
5. Change detection  : bank erosion of stable land, loss of chars and sandbars
                       inside the river belt, accretion, vegetation loss,
                       new bare soil, and bank retreat distance
6. Hotspot table     : change and high-risk patches ranked for field visits
"""
import numpy as np
import xarray as xr


def _safe_ratio(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return xr.where((a + b) != 0, (a - b) / (a + b), np.nan)


def indices(s2):
    """s2: Dataset with reflectance bands B02..B12 (0-1)."""
    ndvi = _safe_ratio(s2["B08"], s2["B04"])
    mndwi = _safe_ratio(s2["B03"], s2["B11"])
    bsi = _safe_ratio(s2["B11"] + s2["B04"], s2["B08"] + s2["B02"])
    return xr.Dataset({"NDVI": ndvi, "MNDWI": mndwi, "BSI": bsi})


def slope_deg(elev, res, smooth_m=0):
    """
    Slope in degrees. The Copernicus DEM is a surface model, so tree lines, tea-garden
    shade trees and buildings create false slopes on flat land. A moving-average filter
    of `smooth_m` metres removes this canopy noise before the slope is computed.
    """
    z = elev.values.astype("float64")
    if smooth_m and smooth_m > res:
        from scipy.ndimage import uniform_filter
        size = int(round(smooth_m / res)) | 1
        valid = np.isfinite(z)
        zf = np.where(valid, z, 0.0)
        num = uniform_filter(zf, size=size, mode="nearest")
        den = uniform_filter(valid.astype("float64"), size=size, mode="nearest")
        z = np.where(valid, num / np.maximum(den, 1e-6), np.nan)
    dzdy, dzdx = np.gradient(z, res, res)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return elev.copy(data=slope.astype("float32")).rename("slope_deg")


def ls_factor(slope, res, slope_length_cap=150.0):
    """
    Slope length and steepness factor (McCool et al. form).
    Slope length is approximated by the cell size, which is a simple proxy.
    For hill areas, replace it with flow accumulation (e.g. pysheds/WhiteboxTools).
    """
    theta = np.radians(slope)
    s = np.sin(theta)
    S = xr.where(np.tan(theta) < 0.09, 10.8 * s + 0.03, 16.8 * s - 0.50)
    beta = (s / 0.0896) / (3 * s ** 0.8 + 0.56)
    m = beta / (1 + beta)
    L = (min(res, slope_length_cap) / 22.13) ** m
    return (L * S).clip(min=0).rename("LS")


def c_factor(ndvi, alpha=2.0, beta=1.0):
    """Cover management factor from NDVI (Van der Knijff et al., 2000)."""
    n = ndvi.clip(-0.99, 0.99)
    c = np.exp(-alpha * n / (beta - n))
    return c.clip(0, 1).rename("C")


def _raster_on(template, path, fill):
    """Read a raster, resample it onto the analysis grid, fill gaps with `fill`.
    Returns (DataArray, used_file: bool). Falls back to the constant if the file is absent."""
    from pathlib import Path
    from rasterio.enums import Resampling
    if path and Path(path).exists():
        import rioxarray
        da = rioxarray.open_rasterio(path, masked=True).squeeze("band", drop=True)
        da = da.rio.reproject_match(template, resampling=Resampling.bilinear)
        da = da.assign_coords(x=template.x, y=template.y)
        return da.fillna(float(da.mean(skipna=True)) if np.isfinite(da).any() else fill), True
    return xr.full_like(template, fill, dtype="float32"), False


def r_factor(template, annual_rainfall_mm, rainfall_raster=None):
    """Rainfall erosivity from mean annual rainfall P (mm): R = 79 + 0.363 P
    (Singh et al., 1981, for Indian conditions)."""
    p, used = _raster_on(template, rainfall_raster, annual_rainfall_mm)
    return (79 + 0.363 * p).astype("float32").rename("R"), used, p


def k_factor(template, value, raster=None):
    k, used = _raster_on(template, raster, value)
    return k.astype("float32").rename("K"), used


def rusle(ind, elev, cfg, res, water_mask):
    rc = cfg["rusle"]
    slope = slope_deg(elev, res, rc.get("dem_smooth_m", 0))
    LS = ls_factor(slope, res)
    C = c_factor(ind["NDVI"])
    R, r_used, P = r_factor(elev, rc["annual_rainfall_mm"], rc.get("rainfall_raster"))
    K, k_used = k_factor(elev, rc["k_factor"], rc.get("k_raster"))
    A = (R * K * LS * C * rc["p_factor"]).where(~water_mask)
    ds = xr.Dataset({"slope_deg": slope, "LS": LS, "C": C, "R": R, "K": K, "P_mm": P,
                     "soil_loss_t_ha_yr": A.astype("float32")})
    ds.attrs.update(rainfall_from_file=int(r_used), k_from_file=int(k_used))
    return ds


def classify(soil_loss, thresholds):
    cls = xr.full_like(soil_loss, np.nan)
    edges = [-np.inf] + list(thresholds) + [np.inf]
    for i in range(5):
        cls = xr.where((soil_loss > edges[i]) & (soil_loss <= edges[i + 1]), i + 1, cls)
    return cls.where(np.isfinite(soil_loss)).rename("risk_class")


# JRC Global Surface Water transitions 1984-2021, regrouped for the dashboard
HISTORY_LABELS = {1: "Land to permanent water", 2: "Land to seasonal water",
                  3: "Permanent water to land", 4: "Seasonal water to land"}
_JRC_TO_HISTORY = {2: 1, 5: 2, 3: 3, 6: 4}


def jrc_transitions(template, path):
    """Raw JRC transition codes on the analysis grid (nearest), or None if absent.
    0 = never water 1984-2021, 1-10 = water at some time, 255 = no data."""
    from pathlib import Path
    from rasterio.enums import Resampling
    if not path or not Path(path).exists():
        return None
    import rioxarray
    da = rioxarray.open_rasterio(path).squeeze("band", drop=True)
    da = da.rio.reproject_match(template, resampling=Resampling.nearest, nodata=255)
    return da.values


def history_layer(template, path, raw=None):
    """Regroup JRC transitions into the four dashboard classes.
    Returns None if the file has not been prepared yet."""
    v = jrc_transitions(template, path) if raw is None else raw
    if v is None:
        return None
    out = np.zeros(v.shape, dtype="uint8")
    for jrc, cls in _JRC_TO_HISTORY.items():
        out[v == jrc] = cls
    return template.copy(data=out).astype("uint8").rename("history_class")


RISK_LABELS = {1: "Very low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very high"}


def otsu(values, bins=256):
    """Otsu (1979) threshold that best separates a two-class histogram."""
    v = np.asarray(values, dtype="float64")
    v = v[np.isfinite(v)]
    hist, edges = np.histogram(v, bins=bins)
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1)
    m1 = (np.sum(hist * mids) - np.cumsum(hist * mids)) / np.maximum(w1, 1)
    between = (w0 * w1 * (m0 - m1) ** 2)[:-1]
    # When classes are well separated the maximum is a plateau; take its centre.
    best = np.nonzero(between >= between.max() * (1 - 1e-6))[0]
    return float(mids[int(round(best.mean()))])


def edge_otsu(index, first_guess, water_high=True, buffer_px=3, bounds=None, min_px=2000):
    """
    Edge-based Otsu threshold (after Donchyts et al., 2016). Over a whole district
    the land class is far larger than the water class, which biases plain Otsu.
    The histogram is therefore taken only from a band of pixels on either side of
    the first-guess water edge, where both classes are present in similar amounts.
    Returns (threshold, n_pixels_used).
    """
    from scipy.ndimage import binary_dilation, binary_erosion
    a = np.asarray(index, dtype="float64")
    ok = np.isfinite(a)
    w = (a > first_guess) if water_high else (a < first_guess)
    w &= ok
    zone = binary_dilation(w, iterations=buffer_px) & ~binary_erosion(w, iterations=buffer_px) & ok
    vals = a[zone]
    if vals.size < min_px:
        return float(first_guess), int(vals.size)
    lo, hi = np.nanpercentile(vals, [1, 99])
    t = otsu(np.clip(vals, lo, hi))
    if bounds is not None:
        t = float(np.clip(t, *bounds))
    return t, int(vals.size)


def _nearest(values, t):
    i = int(np.argmin([abs(v - t) for v in values]))
    return i, values[i]


def water_from_frequency(ind, s1, opt_counts, sar_counts, cc):
    """
    Water map for one period.

    1. Thresholds: edge-based Otsu on the median MNDWI composite (optical) and on
       the median VV composite (radar), limited to sensible ranges.
    2. Frequency: for each pixel, the share of its clear dates on which it was
       water at that threshold. A pixel is water if this share is at least
       `water_frequency_min` (default 0.5), i.e. it was water on most dates.
    3. Sensor choice: optical where a pixel has at least `min_observations`
       clear dates. Radar is used only where optical data are lacking, because
       dry sand on the chars is dark on radar and can be mistaken for water.
    Falls back to the composite with the Otsu threshold if counts are absent.
    Returns (water bool DataArray, frequency DataArray, info dict).
    """
    from acquire import OPT_THRESHOLDS, OPT_COUNT_BANDS, SAR_THRESHOLDS, SAR_COUNT_BANDS
    fmin = float(cc.get("water_frequency_min", 0.5))
    nmin = int(cc.get("min_observations", 2))
    mndwi = ind["MNDWI"]
    t_opt, n_edge = edge_otsu(mndwi.values, cc["mndwi_water"], True,
                              bounds=cc.get("mndwi_bounds", [-0.3, 0.3]))
    info = {"mndwi_threshold_otsu": round(t_opt, 3), "edge_pixels_optical": n_edge}
    freq = xr.full_like(mndwi, np.nan, dtype="float32")
    water = xr.zeros_like(mndwi, dtype=bool)
    src = xr.zeros_like(mndwi, dtype="uint8")          # 1 optical, 2 radar, 3 composite

    if opt_counts is not None:
        i, t_used = _nearest(OPT_THRESHOLDS, t_opt)
        n = opt_counts["n_clear"].astype("float32")
        f = opt_counts[OPT_COUNT_BANDS[i + 1]].astype("float32") / n.where(n > 0)
        use = n >= nmin
        freq = xr.where(use, f, freq)
        water = xr.where(use, f >= fmin, water)
        src = xr.where(use, 1, src)
        info["mndwi_threshold_used"] = t_used
    else:
        use = xr.zeros_like(water)

    have_sar = s1 is not None and "VV_dB" in s1
    if have_sar:
        t_sar, n_edge_s = edge_otsu(s1["VV_dB"].values, cc["sar_water_db"], False,
                                    bounds=cc.get("sar_bounds", [-22.0, -13.0]))
        info.update(vv_threshold_otsu=round(t_sar, 2), edge_pixels_radar=n_edge_s)
        if sar_counts is not None:
            j, ts_used = _nearest(SAR_THRESHOLDS, t_sar)
            ns = sar_counts["n_obs"].astype("float32")
            fs = sar_counts[SAR_COUNT_BANDS[j + 1]].astype("float32") / ns.where(ns > 0)
            use_s = (~use) & (ns >= nmin)
            freq = xr.where(use_s, fs, freq)
            water = xr.where(use_s, fs >= fmin, water)
            src = xr.where(use_s, 2, src)
            info["vv_threshold_used"] = ts_used
            use = use | use_s

    # Remaining pixels: single composite with the adaptive thresholds
    rest = ~use
    comp = mndwi > t_opt
    if have_sar:
        comp = xr.where(np.isfinite(mndwi), comp, s1["VV_dB"] < info["vv_threshold_otsu"])
    water = xr.where(rest, comp, water)
    src = xr.where(rest & (np.isfinite(mndwi) | (s1["VV_dB"].notnull() if have_sar else False)), 3, src)

    valid = int((src > 0).sum()) or 1
    info["share_from_optical_frequency_pct"] = round(100 * int((src == 1).sum()) / valid, 1)
    info["share_from_radar_frequency_pct"] = round(100 * int((src == 2).sum()) / valid, 1)
    info["share_from_composite_pct"] = round(100 * int((src == 3).sum()) / valid, 1)
    info["frequency_min"] = fmin
    info["min_observations"] = nmin
    return water.astype(bool), freq.rename("water_frequency"), info


def water_mask(ind, s1, cc):
    """Simple water map (composite only). Kept for the notebook version."""
    w = ind["MNDWI"] > cc["mndwi_water"]
    if s1 is not None and "VV_dB" in s1:
        sar_w = s1["VV_dB"] < cc["sar_water_db"]
        w = xr.where(np.isfinite(ind["MNDWI"]), w, sar_w)
    return w.astype(bool)


def change_detection(base, curr, cc, jrc=None):
    """
    Returns an integer change map:
      1 = bank erosion of stable land (land to water; the land had not been
          water at any time 1984-2021 in the JRC record)
      2 = land gained from water (deposition / accretion)
      3 = vegetation loss on land
      4 = new bare soil on land
      5 = char or sandbar lost (land to water inside the historical river belt)
    Without the JRC layer, all land-to-water change is class 1.
    """
    wb, wc = base["water"], curr["water"]
    dndvi = curr["ind"]["NDVI"] - base["ind"]["NDVI"]
    new_bare = (curr["ind"]["BSI"] > 0.05) & (base["ind"]["BSI"] <= 0.05)
    out = xr.zeros_like(dndvi, dtype="uint8")
    out = xr.where(~wc & new_bare, 4, out)
    out = xr.where(~wc & ~wb & (dndvi < cc["ndvi_loss"]), 3, out)
    out = xr.where(~wb & wc, 1, out)
    out = xr.where(wb & ~wc, 2, out)
    if jrc is not None:
        belt = (jrc >= 1) & (jrc <= 10)          # water at some time since 1984
        out = xr.where((out == 1) & belt, 5, out)
    return out.astype("uint8").rename("change_class"), dndvi.rename("dNDVI")


CHANGE_LABELS = {1: "Bank erosion (stable land to water)", 2: "Accretion (water to land)",
                 3: "Vegetation loss", 4: "New bare soil",
                 5: "Char or sandbar lost (within river belt)"}


def retreat_distance(base_water, res):
    """Distance (m) from each pixel to the nearest baseline water pixel. For a
    pixel that has since turned into water, this is how far the bank moved back."""
    from scipy.ndimage import distance_transform_edt
    wb = np.asarray(base_water, dtype=bool)
    if not wb.any():
        return xr.full_like(base_water, np.nan, dtype="float32")
    d = distance_transform_edt(~wb) * res
    return base_water.copy(data=d.astype("float32")).rename("retreat_m")


def patches(class_map, labels, min_pixels, res, extra=None, extra_max=None, max_name="max_retreat_m"):
    """Vectorise class patches into a GeoDataFrame with area and centroid."""
    import geopandas as gpd
    from rasterio.features import shapes, sieve
    from shapely.geometry import shape

    arr = np.nan_to_num(class_map.values, nan=0).astype("int32")
    arr = sieve(arr, size=min_pixels, connectivity=8)
    transform = class_map.rio.transform()
    recs = []
    for geom, val in shapes(arr, mask=arr > 0, transform=transform, connectivity=8):
        val = int(val)
        if val not in labels:
            continue
        g = shape(geom)
        recs.append({"class_id": val, "class": labels[val], "geometry": g,
                     "area_ha": g.area / 10000.0})
    if not recs:
        return gpd.GeoDataFrame({"class_id": [], "class": [], "area_ha": []},
                                geometry=[], crs=class_map.rio.crs)
    gdf = gpd.GeoDataFrame(recs, geometry="geometry", crs=class_map.rio.crs)
    if gdf.empty:
        return gdf
    if extra is not None or extra_max is not None:
        from scipy.ndimage import labeled_comprehension
        from rasterio.features import rasterize
        ids = rasterize(((g, i + 1) for i, g in enumerate(gdf.geometry)),
                        out_shape=arr.shape, transform=transform, fill=0, dtype="int32")
        idx = np.arange(1, len(gdf) + 1)

        def per_patch(values, fn):
            v = np.asarray(values, dtype="float64")
            ok = np.isfinite(v) & (ids > 0)
            return labeled_comprehension(np.where(ok, v, np.nan), np.where(ok, ids, 0), idx,
                                         lambda a: fn(a) if a.size else np.nan, float, np.nan)
        if extra is not None:
            gdf["mean_soil_loss_t_ha_yr"] = np.round(per_patch(extra.values, np.mean), 2)
        if extra_max is not None:
            gdf[max_name] = np.round(per_patch(extra_max.values, np.max), 0)
    cent = gdf.geometry.centroid.to_crs("EPSG:4326")
    gdf["lat"], gdf["lon"] = cent.y.round(6), cent.x.round(6)
    return gdf


def rank_hotspots(change_gdf, risk_gdf, top_n=50):
    """
    Priority score for field verification:
      bank erosion and very high risk patches score highest, scaled by area.
    """
    import pandas as pd
    weights = {CHANGE_LABELS[1]: 1.0, "Very high": 0.9, "Vegetation loss": 0.6,
               "New bare soil": 0.6, "High": 0.5, CHANGE_LABELS[5]: 0.4,
               "Accretion (water to land)": 0.3}
    frames = []
    for gdf, src in [(change_gdf, "change"), (risk_gdf, "risk")]:
        if gdf is None or gdf.empty:
            continue
        g = gdf[gdf["class"].isin(weights)].copy()
        g["source"] = src
        frames.append(g)
    if not frames:
        return None
    allp = pd.concat(frames, ignore_index=True)
    allp["priority_score"] = allp["class"].map(weights) * np.log1p(allp["area_ha"])
    allp = allp.sort_values("priority_score", ascending=False).head(top_n).reset_index(drop=True)
    allp.insert(0, "rank", allp.index + 1)
    allp["priority_score"] = allp["priority_score"].round(3)
    allp["area_ha"] = allp["area_ha"].round(2)
    return allp
