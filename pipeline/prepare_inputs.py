"""
One-time preparation of the static RUSLE and history inputs for Dibrugarh.

Run by the "Prepare static inputs" workflow (or locally):
    python pipeline/prepare_inputs.py --start 2000 --end 2025

Outputs in data/ (small district windows, committed to the repository):
    rainfall_imd_mean_annual.tif     IMD 0.25 deg gridded rainfall, mean annual total (mm)
    rainfall_chirps_mean_annual.tif  CHIRPS v2.0 0.05 deg, mean annual total (mm), cross-check
    k_factor_soilgrids.tif           RUSLE K (t ha h / ha MJ mm) from SoilGrids topsoil,
                                     Williams (1995) EPIC equation, 250 m
    jrc_gsw_transitions.tif          JRC Global Surface Water v1.4 transitions 1984-2021, 30 m
    inputs_report.json               summary statistics of all inputs

Each part is independent: if one source is unreachable the others are still built,
and the monitoring run falls back to the constants in config.yaml for anything missing.
"""
import argparse
import datetime as dt
import json
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import rioxarray  # noqa: F401
import xarray as xr
from rasterio.windows import from_bounds

DATA = Path("data")
BOUNDARY = DATA / "dibrugarh_boundary.geojson"
UTM = "EPSG:32646"


def district():
    gdf = gpd.read_file(BOUNDARY).to_crs("EPSG:4326")
    return gdf, [float(v) for v in gdf.total_bounds]


def district_mean(da, gdf):
    """Area mean of a raster inside the district (any CRS)."""
    try:
        clipped = da.rio.clip(gdf.to_crs(da.rio.crs).geometry, all_touched=True, drop=True)
        return round(float(clipped.mean(skipna=True)), 3)
    except Exception:
        return None


# --------------------------------------------------------------------- rainfall
def imd_rainfall(years, bbox, gdf, pad=0.5):
    """Mean annual rainfall from IMD 0.25 deg daily gridded data (imdlib)."""
    import imdlib as imd
    tmp = Path("outputs/imd_raw")
    tmp.mkdir(parents=True, exist_ok=True)
    totals, used = [], []
    for y in years:
        try:
            imd.get_data("rain", y, y, fn_format="yearwise", file_dir=str(tmp))
            ds = imd.open_data("rain", y, y, "yearwise", str(tmp)).get_xarray()
            v = ds[list(ds.data_vars)[0]]
            sub = v.sel(lat=slice(bbox[1] - pad, bbox[3] + pad),
                        lon=slice(bbox[0] - pad, bbox[2] + pad))
            ndays = int(sub.sizes["time"])
            totals.append(sub.sum("time", min_count=int(0.9 * ndays)))
            used.append(y)
            print(f"  IMD {y}: {ndays} days, district-box mean {float(totals[-1].mean()):.0f} mm")
        except Exception as e:
            print(f"  IMD {y}: skipped ({e})")
        finally:
            for f in tmp.glob("*"):
                f.unlink(missing_ok=True) if f.is_file() else shutil.rmtree(f, ignore_errors=True)
    if not totals:
        raise RuntimeError("no IMD year could be read")
    mean = xr.concat(totals, "year").mean("year", skipna=True).astype("float32")
    mean = (mean.rename({"lat": "y", "lon": "x"}).sortby("y", ascending=False)
            .rio.set_spatial_dims(x_dim="x", y_dim="y").rio.write_crs("EPSG:4326")
            .rio.write_nodata(np.nan))
    out = DATA / "rainfall_imd_mean_annual.tif"
    mean.rio.to_raster(out, compress="deflate")
    return {"file": str(out), "source": "IMD 0.25 deg gridded daily rainfall (Pai et al., 2014) via imdlib",
            "years": [used[0], used[-1]], "n_years": len(used),
            "district_mean_mm": district_mean(mean, gdf)}


def chirps_rainfall(years, bbox, gdf, pad=0.2):
    """Mean annual rainfall from CHIRPS v2.0 global annual 0.05 deg GeoTIFFs (windowed reads)."""
    base = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_annual/tifs/chirps-v2.0.{}.tif"
    arrays, used, profile = [], [], None
    b = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    for y in years:
        try:
            with rasterio.open("/vsicurl/" + base.format(y)) as src:
                win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
                a = src.read(1, window=win).astype("float32")
                a[a <= -9000] = np.nan
                if profile is None:
                    profile = {"transform": src.window_transform(win), "crs": src.crs,
                               "height": a.shape[0], "width": a.shape[1]}
                arrays.append(a)
                used.append(y)
                print(f"  CHIRPS {y}: mean {np.nanmean(a):.0f} mm")
        except Exception as e:
            print(f"  CHIRPS {y}: skipped ({e})")
    if not arrays:
        raise RuntimeError("no CHIRPS year could be read")
    mean = np.nanmean(np.stack(arrays), axis=0).astype("float32")
    out = DATA / "rainfall_chirps_mean_annual.tif"
    with rasterio.open(out, "w", driver="GTiff", dtype="float32", count=1, nodata=np.nan,
                       compress="deflate", **profile) as dst:
        dst.write(mean, 1)
    da = rioxarray.open_rasterio(out, masked=True).squeeze("band", drop=True)
    return {"file": str(out), "source": "CHIRPS v2.0 0.05 deg annual (Funk et al., 2015)",
            "years": [used[0], used[-1]], "n_years": len(used),
            "district_mean_mm": district_mean(da, gdf)}


# ------------------------------------------------------------------------- soil
SOILGRIDS = "https://files.isric.org/soilgrids/latest/data/{p}/{p}_{d}_mean.vrt"
DEPTHS = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}   # thickness weights (topsoil 0-30 cm)


def k_williams(sand, silt, clay, oc):
    """
    RUSLE K from texture and organic carbon (Williams, 1995; EPIC model).
    sand, silt, clay in %, oc = organic carbon in %. Returns SI units
    (t ha h ha-1 MJ-1 mm-1) using the 0.1317 conversion factor.
    """
    sn1 = 1.0 - sand / 100.0
    f_csand = 0.2 + 0.3 * np.exp(-0.256 * sand * (1.0 - silt / 100.0))
    f_clsi = (silt / np.maximum(clay + silt, 1e-6)) ** 0.3
    f_orgc = 1.0 - 0.25 * oc / (oc + np.exp(3.72 - 2.95 * oc))
    f_hisand = 1.0 - 0.7 * sn1 / (sn1 + np.exp(-5.51 + 22.9 * sn1))
    return f_csand * f_clsi * f_orgc * f_hisand * 0.1317


def soilgrids_k(bbox, gdf, res=250, pad=0.05):
    """Topsoil (0-30 cm) texture and SOC from SoilGrids 250 m, converted to RUSLE K."""
    props = {}
    for p in ["sand", "silt", "clay", "soc"]:
        layers, weights = [], []
        for d, w in DEPTHS.items():
            da = rioxarray.open_rasterio("/vsicurl/" + SOILGRIDS.format(p=p, d=d), masked=True)
            da = da.squeeze("band", drop=True).rio.clip_box(
                bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad, crs="EPSG:4326")
            da = da.rio.reproject(UTM, resolution=res, resampling=rasterio.enums.Resampling.bilinear)
            layers.append(da.astype("float32"))
            weights.append(w)
        ref = layers[0]
        stack = xr.concat([l.rio.reproject_match(ref) for l in layers], "depth")
        props[p] = stack.weighted(xr.DataArray(weights, dims="depth")).mean("depth")
        print(f"  SoilGrids {p}: read {len(layers)} depths")
    ref = props["sand"]
    sand = props["sand"] / 10.0                                  # g/kg -> %
    silt = props["silt"].rio.reproject_match(ref) / 10.0
    clay = props["clay"].rio.reproject_match(ref) / 10.0
    oc = props["soc"].rio.reproject_match(ref) / 100.0           # dg/kg -> %
    k = k_williams(sand, silt, clay, oc).astype("float32").rio.write_crs(UTM).rio.write_nodata(np.nan)
    out = DATA / "k_factor_soilgrids.tif"
    k.rio.to_raster(out, compress="deflate")
    return {"file": str(out),
            "source": "SoilGrids 2.0, 250 m (Poggio et al., 2021); topsoil 0-30 cm; "
                      "K by Williams (1995) EPIC equation",
            "district_mean_K": district_mean(k, gdf),
            "district_mean_sand_pct": district_mean(sand.rio.write_crs(UTM), gdf),
            "district_mean_clay_pct": district_mean(clay.rio.write_crs(UTM), gdf),
            "district_mean_oc_pct": district_mean(oc.rio.write_crs(UTM), gdf)}


# ---------------------------------------------------------------------- history
JRC_URLS = [
    "https://storage.googleapis.com/global-surface-water/downloads2021/transitions/transitions_{t}v1_4_2021.tif",
    "https://storage.googleapis.com/global-surface-water/downloads2021/transition/transition_{t}v1_4_2021.tif",
    "https://storage.googleapis.com/global-surface-water/downloads2/transitions/transitions_{t}_v1_1_2019.tif",
]
JRC_CLASSES = {1: "Permanent water", 2: "New permanent water", 3: "Lost permanent water",
               4: "Seasonal water", 5: "New seasonal water", 6: "Lost seasonal water",
               7: "Seasonal to permanent", 8: "Permanent to seasonal",
               9: "Ephemeral permanent", 10: "Ephemeral seasonal"}


def jrc_history(bbox, gdf, pad=0.02):
    """
    JRC Global Surface Water transitions between the first (1984) and last (2021) year.
    Land that became water (classes 2 and 5) is a satellite record of bank erosion;
    water that became land (3 and 6) records accretion.
    """
    lon0 = int(np.floor(bbox[0] / 10.0) * 10)
    lat0 = int(np.ceil(bbox[3] / 10.0) * 10)
    tile = f"{abs(lon0)}{'E' if lon0 >= 0 else 'W'}_{abs(lat0)}{'N' if lat0 >= 0 else 'S'}"
    b = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    last = None
    for tmpl in JRC_URLS:
        url = tmpl.format(t=tile)
        try:
            with rasterio.open("/vsicurl/" + url) as src:
                win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
                a = src.read(1, window=win)
                profile = {"transform": src.window_transform(win), "crs": src.crs,
                           "height": a.shape[0], "width": a.shape[1]}
            print(f"  JRC transitions: read {a.shape} from {url}")
            break
        except Exception as e:
            last = e
            print(f"  JRC: {url} not available ({e})")
    else:
        raise RuntimeError(f"JRC transitions not reachable ({last})")
    out = DATA / "jrc_gsw_transitions.tif"
    with rasterio.open(out, "w", driver="GTiff", dtype="uint8", count=1, nodata=255,
                       compress="deflate", **profile) as dst:
        dst.write(a.astype("uint8"), 1)
    # Areas inside the district on a 30 m UTM grid
    da = rioxarray.open_rasterio(out).squeeze("band", drop=True)
    da = da.rio.reproject(UTM, resolution=30, resampling=rasterio.enums.Resampling.nearest)
    da = da.rio.clip(gdf.to_crs(UTM).geometry, drop=True)
    v = da.values
    ha = 30 * 30 / 1e4
    areas = {JRC_CLASSES[k]: round(float((v == k).sum()) * ha, 1) for k in JRC_CLASSES}
    return {"file": str(out), "source": "JRC Global Surface Water v1.4 (Pekel et al., 2016), "
            "transitions 1984-2021, 30 m", "tile": tile, "area_ha": areas,
            "land_to_water_ha": round(areas["New permanent water"] + areas["New seasonal water"], 1),
            "water_to_land_ha": round(areas["Lost permanent water"] + areas["Lost seasonal water"], 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2000)
    ap.add_argument("--end", type=int, default=2025)
    ap.add_argument("--only", nargs="*", default=["imd", "chirps", "soil", "jrc"])
    args = ap.parse_args()
    years = list(range(args.start, args.end + 1))
    gdf, bbox = district()
    print("District bbox:", bbox)

    rp = DATA / "inputs_report.json"
    report = json.loads(rp.read_text()) if rp.exists() else {}
    steps = {"imd": ("rainfall_imd", lambda: imd_rainfall(years, bbox, gdf)),
             "chirps": ("rainfall_chirps", lambda: chirps_rainfall(years, bbox, gdf)),
             "soil": ("k_factor", lambda: soilgrids_k(bbox, gdf)),
             "jrc": ("history_jrc", lambda: jrc_history(bbox, gdf))}
    for key in args.only:
        name, fn = steps[key]
        print(f"\n== {name}")
        try:
            report[name] = fn()
            report[name]["prepared"] = dt.date.today().isoformat()
            print("  ok:", {k: v for k, v in report[name].items() if k != "area_ha"})
        except Exception as e:
            print(f"  FAILED: {e}")
            report.setdefault(name, {})["error"] = str(e)
    imd_m = report.get("rainfall_imd", {}).get("district_mean_mm")
    ch_m = report.get("rainfall_chirps", {}).get("district_mean_mm")
    if imd_m and ch_m:
        report["rainfall_crosscheck"] = {"imd_mm": imd_m, "chirps_mm": ch_m,
                                         "difference_pct": round(100 * (ch_m - imd_m) / imd_m, 1)}
    rp.write_text(json.dumps(report, indent=2))
    print("\nReport written to", rp)


if __name__ == "__main__":
    main()
