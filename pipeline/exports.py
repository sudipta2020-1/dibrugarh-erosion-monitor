"""Download files for the SWiFT-Bank results (GeoTIFF and Shapefile).

Called at the end of pipeline/swift.py. Writes the files to
outputs/<project>/swift_release/, which the workflow publishes as the GitHub
release "swift-latest", and a list of the files to docs/data/swift/downloads.json,
which the Downloads panel on every page reads.

Rasters are in UTM zone 46N (EPSG:32646) at 10 m and cover the river corridor.
"""
import json
import time
import zipfile
from pathlib import Path

import numpy as np

TAG = "swift-latest"
CLASS_CODES = {0: "No tested change", 1: "Bank erosion (stable land to water)", 2: "Accretion (water to land)",
               5: "Char or sandbar lost (within river belt)", 6: "New inland water (pond or flooding)", 255: "Outside the river corridor"}


def _tif(tmpl, arr, dtype, nodata, path):
    da = tmpl.copy(data=arr.astype(dtype)).rio.write_nodata(nodata)
    da.rio.to_raster(path, compress="deflate", predictor=2 if np.dtype(dtype).kind in "iu" else 3,
                     tiled=True, blockxsize=512, blockysize=512)


def _zip_shp(gdf, stem, folder):
    """Write a shapefile and zip its parts into <stem>_shp.zip."""
    tmp = folder / f"_{stem}"
    tmp.mkdir(exist_ok=True)
    gdf.to_file(tmp / f"{stem}.shp", driver="ESRI Shapefile", encoding="utf-8")
    z = folder / f"{stem}_shp.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(tmp.iterdir()):
            zf.write(f, f.name)
            f.unlink()
    tmp.rmdir()
    return z


def swift_files(cfg, ctx):
    """ctx: tmpl, inside, c, d, wf_base, wf_cur, mdc_m2, P (patches, UTM), lb, lc (bank lines), periods, pub, dash."""
    import geopandas as gpd

    folder = Path(cfg["output_dir"]) / cfg["project_name"] / "swift_release"
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.iterdir():
        f.unlink()
    tmpl, inside = ctx["tmpl"], ctx["inside"]
    crs = cfg["crs"]
    per = ctx["periods"]
    items = []

    def add(name, label, fmt, group):
        items.append({"name": name, "label": label, "format": fmt, "group": group,
                      "size_mb": round((folder / name).stat().st_size / 1e6, 2)})

    # 1. Rasters
    c = np.where(inside, ctx["c"], 255)
    _tif(tmpl, c, "uint8", 255, folder / "swift_change_class_10m.tif")
    add("swift_change_class_10m.tif", "Change classes, tested (10 m)", "GeoTIFF", "swift")

    d = np.where(inside, np.clip(np.round(np.nan_to_num(ctx["d"]) * 100), -100, 100), -128)
    _tif(tmpl, d, "int8", -128, folder / "swift_water_fraction_change_10m.tif")
    add("swift_water_fraction_change_10m.tif", "Change in water share of each pixel, % (10 m)", "GeoTIFF", "swift")

    for key, tag in (("wf_base", "baseline"), ("wf_cur", "current")):
        w = ctx[key]
        ok = inside & np.isfinite(w)
        v = np.where(ok, np.clip(np.round(np.nan_to_num(w) * 100), 0, 100), 255)
        name = f"swift_water_fraction_{tag}_10m.tif"
        _tif(tmpl, v, "uint8", 255, folder / name)
        add(name, f"Mean water share of each pixel, {tag} window {per[tag][0]} to {per[tag][1]}, % (10 m)", "GeoTIFF", "swift")

    m = ctx["mdc_m2"]
    ok = inside & np.isfinite(m)
    v = np.where(ok, np.clip(np.round(np.nan_to_num(m)), 0, 254), 255)
    _tif(tmpl, v, "uint8", 255, folder / "swift_detection_limit_m2_10m.tif")
    add("swift_detection_limit_m2_10m.tif", "Smallest detectable land loss per pixel, m² (10 m)", "GeoTIFF", "swift")

    # 2. Vectors
    P = ctx["P"]
    if len(P):
        g = gpd.GeoDataFrame({
            "loss_ha": P["loss_ha"].round(4), "foot_ha": P["footprint_ha"].round(4),
            "p_value": P["p_value"].astype(float), "class": P["class"].astype(str),
            "mean_chg": P["mean_change"].round(3), "se_mean": P["se_mean"].round(3),
            "retreat_m": P["retreat_subpixel_m"].astype(float).round(1) if "retreat_subpixel_m" in P else np.nan,
        }, geometry=P.geometry, crs=P.crs).to_crs(crs)
        _zip_shp(g, "swift_loss_patches", folder)
        add("swift_loss_patches_shp.zip", "Land-loss patches with lost land and p-value", "Shapefile", "swift")

    lines = []
    for tag, ls in (("baseline", ctx["lb"]), ("current", ctx["lc"])):
        for ln in ls:
            lines.append({"window": tag, "start": per[tag][0], "end": per[tag][1], "geometry": ln})
    if lines:
        gl = gpd.GeoDataFrame(lines, geometry="geometry", crs=crs)
        gl["length_m"] = gl.length.round(1)
        _zip_shp(gl, "swift_bank_lines", folder)
        add("swift_bank_lines_shp.zip", "Sub-pixel bank lines, baseline and current", "Shapefile", "swift")

    hp = ctx["dash"] / "hotspots.geojson"
    if hp.exists():
        h = gpd.read_file(hp)
        ren = {"rank": "rank", "site_id": "site_id", "class": "class", "urgency": "urgency", "lead_agency": "agency",
               "recommended_action": "action", "area_ha": "area_ha", "p_value": "p_value", "retreat_m_per_yr": "retreat_my",
               "confidence": "confidence", "population": "people", "nearest_place": "near_place",
               "priority_score": "priority", "lat": "lat", "lon": "lon"}
        keep = [k for k in ren if k in h.columns]
        hs = h[keep + ["geometry"]].rename(columns=ren)
        if "action" in hs:
            hs["action"] = hs["action"].astype(str).str.slice(0, 254)
        _zip_shp(hs.to_crs(crs), "priority_sites", folder)
        add("priority_sites_shp.zip", "Priority sites for field verification", "Shapefile", "sites")

    # 3. Read-me
    readme = [
        "SWiFT-Bank data files",
        f"Run: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        f"Baseline window: {per['baseline'][0]} to {per['baseline'][1]}; current window: {per['current'][0]} to {per['current'][1]}",
        f"Coordinate system: {crs} (UTM zone 46N). Raster cell size: 10 m. Rasters cover the river corridor only.",
        "",
        "swift_change_class_10m.tif  (uint8, nodata 255)",
        *[f"    {k} = {v}" for k, v in CLASS_CODES.items() if k != 255],
        "swift_water_fraction_change_10m.tif  (int8, nodata -128): change in mean water share, current minus baseline, in percent of the pixel.",
        "    A value of 40 means that 40 % of the 100 m2 pixel (40 m2) turned from land to water.",
        "swift_water_fraction_baseline_10m.tif, swift_water_fraction_current_10m.tif  (uint8, nodata 255): mean water share of each pixel over the clear dates of the window, percent.",
        "swift_detection_limit_m2_10m.tif  (uint8, nodata 255): smallest land loss in a pixel that the test detects with 80 % power at the 5 % level, m2 (capped at 254).",
        "",
        "swift_loss_patches_shp.zip: loss_ha = land lost to the river (sum of water-share change x pixel area); foot_ha = patch area;",
        "    p_value = combined p-value of the patch (Stouffer); class = bank erosion or char loss; mean_chg = mean water-share change;",
        "    se_mean = mean standard error; retreat_m = largest distance between the baseline and current sub-pixel bank lines.",
        "swift_bank_lines_shp.zip: 0.5 water-share contour of each window (sub-pixel bank line). window = baseline or current.",
        "priority_sites_shp.zip: ranked sites from the dashboard. agency = lead agency; action = recommended action (shortened to 254 characters);",
        "    retreat_my = bank retreat, m per year; people = people within 500 m (WorldPop 2020, upper estimate); near_place = nearest named place.",
        "",
        "Method: water share from local end-member unmixing of green, NIR and sharpened SWIR on every clear Sentinel-2 date; per-pixel Welch t-test",
        "between windows with the false discovery rate held at 5 %; patches kept if lost land >= 0.02 ha and patch p < 0.01.",
        "Data: Copernicus Sentinel-2 (via Microsoft Planetary Computer), JRC Global Surface Water, WorldPop 2020, OpenStreetMap.",
        "Results should be checked in the field before works are planned.",
    ]
    (folder / "README_swift_data.txt").write_text("\n".join(readme) + "\n")
    add("README_swift_data.txt", "Read me: what each file contains", "Text", "swift")

    man = {"tag": TAG, "repository": ctx.get("repository", ""), "run": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "periods": per, "crs": crs, "files": items}
    (ctx["pub"] / "downloads.json").write_text(json.dumps(man, indent=1))
    print(f"  wrote {len(items)} download files to {folder}")
    return items
