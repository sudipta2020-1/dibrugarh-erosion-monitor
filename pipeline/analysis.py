"""
Erosion analysis on the acquired layers.

1. Spectral indices  : NDVI, MNDWI, BSI (bare soil index)
2. RUSLE soil loss   : A = R * K * LS * C * P   (t/ha/yr)
3. Risk classes      : 1 very low ... 5 very high
4. Change detection  : riverbank land loss / gain, vegetation loss,
                       new bare soil between the two periods
5. Hotspot table     : change and high-risk patches ranked for field visits
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


def slope_deg(elev, res):
    dzdy, dzdx = np.gradient(elev.values.astype("float64"), res, res)
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


def r_factor(template, annual_rainfall_mm, rainfall_raster=None):
    if rainfall_raster:
        import rioxarray
        p = rioxarray.open_rasterio(rainfall_raster).squeeze().rio.reproject_match(template)
    else:
        p = xr.full_like(template, annual_rainfall_mm, dtype="float32")
    return (79 + 0.363 * p).rename("R")


def k_factor(template, value, raster=None):
    if raster:
        import rioxarray
        return rioxarray.open_rasterio(raster).squeeze().rio.reproject_match(template).rename("K")
    return xr.full_like(template, value, dtype="float32").rename("K")


def rusle(ind, elev, cfg, res, water_mask):
    rc = cfg["rusle"]
    slope = slope_deg(elev, res)
    LS = ls_factor(slope, res)
    C = c_factor(ind["NDVI"])
    R = r_factor(elev, rc["annual_rainfall_mm"], rc.get("rainfall_raster"))
    K = k_factor(elev, rc["k_factor"], rc.get("k_raster"))
    A = (R * K * LS * C * rc["p_factor"]).where(~water_mask)
    return xr.Dataset({"slope_deg": slope, "LS": LS, "C": C, "R": R, "K": K,
                       "soil_loss_t_ha_yr": A.astype("float32")})


def classify(soil_loss, thresholds):
    cls = xr.full_like(soil_loss, np.nan)
    edges = [-np.inf] + list(thresholds) + [np.inf]
    for i in range(5):
        cls = xr.where((soil_loss > edges[i]) & (soil_loss <= edges[i + 1]), i + 1, cls)
    return cls.where(np.isfinite(soil_loss)).rename("risk_class")


RISK_LABELS = {1: "Very low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very high"}


def water_mask(ind, s1, cc):
    """Water from optical MNDWI, filled with radar where optical data are missing."""
    w = ind["MNDWI"] > cc["mndwi_water"]
    if s1 is not None and "VV_dB" in s1:
        sar_w = s1["VV_dB"] < cc["sar_water_db"]
        w = xr.where(np.isfinite(ind["MNDWI"]), w, sar_w)
    return w.astype(bool)


def change_detection(base, curr, cc):
    """
    Returns an integer change map:
      1 = land lost to water (bank erosion)
      2 = land gained from water (deposition / accretion)
      3 = vegetation loss on land
      4 = new bare soil on land
    """
    wb, wc = base["water"], curr["water"]
    dndvi = curr["ind"]["NDVI"] - base["ind"]["NDVI"]
    new_bare = (curr["ind"]["BSI"] > 0.05) & (base["ind"]["BSI"] <= 0.05)
    out = xr.zeros_like(dndvi, dtype="uint8")
    out = xr.where(~wc & new_bare, 4, out)
    out = xr.where(~wc & ~wb & (dndvi < cc["ndvi_loss"]), 3, out)
    out = xr.where(~wb & wc, 1, out)
    out = xr.where(wb & ~wc, 2, out)
    return out.astype("uint8").rename("change_class"), dndvi.rename("dNDVI")


CHANGE_LABELS = {1: "Bank erosion (land to water)", 2: "Accretion (water to land)",
                 3: "Vegetation loss", 4: "New bare soil"}


def patches(class_map, labels, min_pixels, res, extra=None):
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
    gdf = gpd.GeoDataFrame(recs, geometry="geometry", crs=class_map.rio.crs)
    if gdf.empty:
        return gdf
    if extra is not None:
        from rasterio.features import rasterize
        ids = rasterize(((g, i + 1) for i, g in enumerate(gdf.geometry)),
                        out_shape=arr.shape, transform=transform, fill=0, dtype="int32")
        vals = extra.values
        means = []
        for i in range(len(gdf)):
            v = vals[ids == i + 1]
            v = v[np.isfinite(v)]
            means.append(float(v.mean()) if v.size else np.nan)
        gdf["mean_soil_loss_t_ha_yr"] = np.round(means, 2)
    cent = gdf.geometry.centroid.to_crs("EPSG:4326")
    gdf["lat"], gdf["lon"] = cent.y.round(6), cent.x.round(6)
    return gdf


def rank_hotspots(change_gdf, risk_gdf, top_n=50):
    """
    Priority score for field verification:
      bank erosion and very high risk patches score highest, scaled by area.
    """
    import pandas as pd
    weights = {"Bank erosion (land to water)": 1.0, "Very high": 0.9, "Vegetation loss": 0.6,
               "New bare soil": 0.6, "High": 0.5, "Accretion (water to land)": 0.3}
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
