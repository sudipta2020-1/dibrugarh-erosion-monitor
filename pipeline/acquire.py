"""
Satellite data acquisition.

Searches the Microsoft Planetary Computer STAC catalogue (free, no account
needed) and builds cloud-free composites on a common grid for the AOI:

  * Sentinel-2 L2A  : surface reflectance (B02, B03, B04, B08, B11, B12)
  * Sentinel-1 RTC  : radar backscatter VV and VH (works through clouds)
  * Copernicus DEM  : 30 m elevation, resampled to the analysis grid

Each composite is saved as a GeoTIFF in outputs/<project>/raw/.

Alongside the median composites, the same pass over the image stack counts,
for every pixel, how many clear observations it had and how many of them were
water at a range of candidate thresholds. These counts give a water frequency
map, which is far less sensitive to one flooded or hazy date than a single
composite (see analysis.water_from_frequency).
"""
import os
from pathlib import Path

# Network robustness for reading cloud-optimised GeoTIFFs: retry transient
# HTTP failures instead of aborting the whole composite.
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "6")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.tiff,.TIF")

import numpy as np
import odc.geo.xr  # noqa: F401  (registers the .odc accessor)
import planetary_computer
import pystac_client
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from odc.stac import load as stac_load

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
S2_BANDS = ["B02", "B03", "B04", "B08", "B11", "B12"]
# Scene classification classes kept as clear: 4 vegetation, 5 bare soil,
# 6 water, 7 unclassified. Clouds, shadows and cirrus are removed.
S2_CLEAR_SCL = [4, 5, 6, 7]


# Candidate water thresholds. Counts are kept for each one so the threshold can
# be chosen afterwards (edge-based Otsu) without reading the images again.
OPT_THRESHOLDS = [round(v, 2) for v in np.arange(-0.30, 0.301, 0.05)]   # MNDWI > t
SAR_THRESHOLDS = [float(v) for v in range(-24, -11)]                    # VV dB < t
OPT_COUNT_BANDS = ["n_clear"] + [f"w_{t:+.2f}" for t in OPT_THRESHOLDS]
SAR_COUNT_BANDS = ["n_obs"] + [f"w_{t:+.0f}" for t in SAR_THRESHOLDS]

GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/"
CHUNKS = {"x": 512, "y": 512}


def get_aoi(cfg):
    """
    Returns (polygon_gdf_or_None, bbox). The polygon is in EPSG:4326.
    Order of preference: local GeoJSON -> geoBoundaries district -> bbox only.
    """
    import geopandas as gpd
    gdf = None
    if cfg.get("aoi_geojson"):
        gdf = gpd.read_file(cfg["aoi_geojson"]).to_crs("EPSG:4326")
        print(f"  AOI from local file {cfg['aoi_geojson']}")
    elif cfg.get("aoi_district"):
        try:
            import requests
            meta = requests.get(GEOBOUNDARIES_API, timeout=60).json()
            url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
            allg = gpd.read_file(url)
            name = cfg["aoi_district"].strip().lower()
            gdf = allg[allg["shapeName"].str.strip().str.lower() == name].to_crs("EPSG:4326")
            if gdf.empty:
                print(f"  District '{cfg['aoi_district']}' not found in geoBoundaries; using bbox.")
                gdf = None
            else:
                print(f"  AOI: {cfg['aoi_district']} district boundary (geoBoundaries ADM2)")
        except Exception as e:  # network or format problem
            print(f"  Could not download district boundary ({e}); using bbox.")
            gdf = None
    if gdf is not None:
        gdf = gdf.dissolve()
        b = gdf.total_bounds
        bbox = [round(float(v), 5) for v in (b[0], b[1], b[2], b[3])]
    else:
        bbox = cfg["aoi_bbox"]
    print(f"  bbox = {bbox}")
    return gdf, bbox


# Planetary Computer access tokens expire after about 45 minutes. Instead of
# signing every URL once at search time, each file is signed at the moment it
# is read (patch_url), so long composites keep working with fresh tokens.
SIGN = planetary_computer.sign


def _catalog():
    return pystac_client.Client.open(STAC_URL)


def _clearest(items, per_tile):
    """Keep the `per_tile` least cloudy scenes for each Sentinel-2 tile."""
    groups = {}
    for it in items:
        groups.setdefault(it.properties.get("s2:mgrs_tile", "all"), []).append(it)
    keep = []
    for tile, its in sorted(groups.items()):
        its.sort(key=lambda i: i.properties.get("eo:cloud_cover", 100))
        keep += its[:per_tile]
    print(f"  using the {len(keep)} clearest scenes ({per_tile} per tile, {len(groups)} tiles)")
    return keep


def _search(collection, bbox, period, query=None):
    items = _catalog().search(
        collections=[collection], bbox=bbox,
        datetime=f"{period[0]}/{period[1]}", query=query or {},
    ).item_collection()
    print(f"  {collection}: {len(items)} scenes found for {period[0]} to {period[1]}")
    return items


def _s2_offset(item):
    # From processing baseline 04.00 (Jan 2022) ESA adds +1000 to the digital numbers.
    pb = item.properties.get("s2:processing_baseline", "00.00")
    return -1000 if pb >= "04.00" else 0


def _s2_products(ds, offsets):
    """Lazy median composite and per-date water counts from a Sentinel-2 stack."""
    clear = ds["SCL"].isin(S2_CLEAR_SCL)
    bands, refl = [], {}
    for b in S2_BANDS:
        dn = ds[b].astype("float32").where((ds[b] > 0) & clear)
        refl[b] = ((dn + np.float32(offsets) if np.isscalar(offsets) else dn + offsets.astype("float32"))
                   / np.float32(10000.0)).clip(0, 1)
        bands.append(refl[b].median("time", skipna=True).rename(b))
    # Per-date water counts (MNDWI = (green - SWIR1) / (green + SWIR1))
    g, sw = refl["B03"], refl["B11"]
    mndwi = (g - sw) / (g + sw)
    ok = np.isfinite(mndwi)
    counts = [ok.sum("time").astype("uint8").rename("n_clear")]
    for t, name in zip(OPT_THRESHOLDS, OPT_COUNT_BANDS[1:]):
        counts.append((ok & (mndwi > t)).sum("time").astype("uint8").rename(name))
    return xr.merge(bands + counts)


def _compute_in_strips(lazy, label, rows=None):
    """
    Compute a lazy dataset one strip of chunk rows at a time. Each strip reads
    every image once for all products, and memory stays bounded by the strip
    size instead of the whole district.
    """
    rows = rows or CHUNKS["y"]
    H = lazy.sizes["y"]
    parts = []
    for i in range(0, H, rows):
        parts.append(lazy.isel(y=slice(i, i + rows)).compute())
        print(f"  {label}: rows {min(i + rows, H)} of {H} done", flush=True)
    return xr.concat(parts, dim="y")


def sentinel2_composite(bbox, period, crs, res, max_cloud, per_tile=15):
    items = _search("sentinel-2-l2a", bbox, period, {"eo:cloud_cover": {"lt": max_cloud}})
    if len(items) == 0:
        raise RuntimeError("No Sentinel-2 scenes found. Widen the period or raise max_cloud_cover.")
    items = _clearest(list(items), per_tile)
    offs = [_s2_offset(it) for it in items]
    if len(set(offs)) == 1:
        # One processing baseline: merge same-day tiles to reduce the stack.
        ds = stac_load(items, bands=S2_BANDS + ["SCL"], bbox=bbox, crs=crs,
                       resolution=res, chunks=CHUNKS, groupby="solar_day",
                       fail_on_error=False, patch_url=SIGN)
        offsets = offs[0]
    else:
        ds = stac_load(items, bands=S2_BANDS + ["SCL"], bbox=bbox, crs=crs,
                       resolution=res, chunks=CHUNKS, fail_on_error=False, patch_url=SIGN)
        offsets = xr.DataArray(offs, dims="time")
    both = _compute_in_strips(_s2_products(ds, offsets), "Sentinel-2")
    out = both[S2_BANDS].rio.write_crs(crs)
    cnt = both[OPT_COUNT_BANDS].rio.write_crs(crs)
    valid = float(np.isfinite(out["B04"]).mean() * 100)
    print(f"  Sentinel-2 composite: {valid:.1f}% of pixels have a clear observation; "
          f"median {float(cnt['n_clear'].median()):.0f} clear dates per pixel")
    return out, cnt


def sentinel1_composite(bbox, period, geobox):
    """Radar composite loaded directly onto the Sentinel-2 grid (no reprojection step)."""
    items = _search("sentinel-1-rtc", bbox, period)
    if len(items) == 0:
        print("  No Sentinel-1 scenes found; radar layers will be skipped.")
        return None, None
    ds = stac_load(items, bands=["vv", "vh"], geobox=geobox,
                   chunks=CHUNKS, groupby="solar_day", fail_on_error=False,
                   patch_url=SIGN)
    out = xr.Dataset()
    for b in ["vv", "vh"]:
        lin = ds[b].astype("float32").where(ds[b] > 0).median("time", skipna=True)
        out[b.upper() + "_dB"] = 10 * np.log10(lin)
    vv = 10 * np.log10(ds["vv"].astype("float32").where(ds["vv"] > 0))
    ok = np.isfinite(vv)
    out["n_obs"] = ok.sum("time").astype("uint8")
    for t, name in zip(SAR_THRESHOLDS, SAR_COUNT_BANDS[1:]):
        out[name] = (ok & (vv < t)).sum("time").astype("uint8")
    both = _compute_in_strips(out, "Sentinel-1")
    crs = str(geobox.crs)
    return both[S1_BANDS].rio.write_crs(crs), both[SAR_COUNT_BANDS].rio.write_crs(crs)


def dem(bbox, geobox):
    """Copernicus DEM resampled directly onto the Sentinel-2 grid."""
    items = _catalog().search(collections=["cop-dem-glo-30"], bbox=bbox).item_collection()
    ds = stac_load(items, bands=["data"], geobox=geobox, resampling="bilinear",
                   chunks=CHUNKS, fail_on_error=False, patch_url=SIGN)
    return (ds["data"].max("time").astype("float32").rename("elevation")
            .compute().rio.write_crs(str(geobox.crs)))


def save(da_or_ds, path, dtype="float32"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = da_or_ds.to_array("band") if isinstance(da_or_ds, xr.Dataset) else da_or_ds
    obj.astype(dtype).rio.to_raster(path, compress="deflate")
    print(f"  saved {path}")


S1_BANDS = ["VV_dB", "VH_dB"]


def open_multiband(path, names):
    masked = not Path(path).name.startswith(("s2count", "s1count"))
    da = rioxarray.open_rasterio(path, masked=masked)
    return xr.Dataset({n: da.isel(band=i, drop=True) for i, n in enumerate(names)})


def acquire_all(cfg, raw_dir, bbox):
    """Download what is missing in raw_dir; reuse composites that already exist."""
    crs, res = cfg["crs"], cfg["resolution_m"]
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    layers = {}
    for tag in ["baseline", "current"]:
        period = cfg[f"period_{tag}"]
        print(f"\n[{tag}] {period[0]} to {period[1]}")
        f2, f1 = raw_dir / f"s2_{tag}.tif", raw_dir / f"s1_{tag}.tif"
        c2, c1 = raw_dir / f"s2count_{tag}.tif", raw_dir / f"s1count_{tag}.tif"
        if f2.exists() and c2.exists():
            print("  reusing", f2.name, "and", c2.name)
            s2, s2c = open_multiband(f2, S2_BANDS), open_multiband(c2, OPT_COUNT_BANDS)
        else:
            s2, s2c = sentinel2_composite(bbox, period, crs, res, cfg["max_cloud_cover"],
                                          cfg.get("max_scenes_per_tile", 15))
            save(s2, f2)
            save(s2c, c2, dtype="uint8")
        if f1.exists() and c1.exists():
            s1, s1c = open_multiband(f1, S1_BANDS), open_multiband(c1, SAR_COUNT_BANDS)
        else:
            s1, s1c = sentinel1_composite(bbox, period, s2["B04"].odc.geobox)
            if s1 is not None:
                save(s1, f1)
                save(s1c, c1, dtype="uint8")
        layers[tag] = {"s2": s2, "s1": s1, "s2_counts": s2c, "s1_counts": s1c}
    fd = raw_dir / "dem.tif"
    if fd.exists():
        elev = rioxarray.open_rasterio(fd, masked=True).squeeze("band", drop=True)
    else:
        print("\n[terrain] Copernicus DEM GLO-30")
        elev = dem(bbox, layers["current"]["s2"]["B04"].odc.geobox)
        save(elev, fd)
    layers["dem"] = elev
    return layers
