"""
Prepare web-ready outputs for the GitHub Pages dashboard.

Writes into docs/data/:
  layers/*.png         map overlays in Web Mercator (for Leaflet)
  layers.json          overlay bounds and legends
  aoi.geojson          district boundary
  hotspots.geojson     ranked field verification sites
  hotspots.csv
  summary.json         latest run statistics
  history.csv          one row per run (time series on the dashboard)

and full-resolution GeoTIFFs into outputs/<project>/release/ for a GitHub release.
"""
import datetime as dt
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import colormaps
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling

import analysis as an

RISK_COLORS = {1: "#1a9850", 2: "#91cf60", 3: "#fee08b", 4: "#fc8d59", 5: "#d73027"}
CHANGE_COLORS = {1: "#d7191c", 5: "#f59ec0", 6: "#66c2a5", 2: "#2c7bb6", 3: "#fdae61", 4: "#8c510a"}
HISTORY_COLORS = {1: "#7a0177", 2: "#dd3497", 3: "#225ea8", 4: "#41b6c4"}
SUSC_COLORS = {1: "#ffffcc", 2: "#c2e699", 3: "#fecc5c", 4: "#fd8d3c", 5: "#bd0026"}
MAX_WIDTH = 1800


def _hex(c):
    c = c.lstrip("#")
    return [int(c[i:i + 2], 16) for i in (0, 2, 4)]


def _to_mercator(da, categorical):
    da = da.rio.write_nodata(np.nan if da.dtype.kind == "f" else 0)
    w = da.sizes["x"]
    res = abs(float(da.x[1] - da.x[0])) * max(1.0, w / MAX_WIDTH)
    return da.rio.reproject("EPSG:3857", resolution=res,
                            resampling=Resampling.nearest if categorical else Resampling.bilinear)


def _bounds_latlon(da):
    x0, y0, x1, y1 = da.rio.bounds()
    t = Transformer.from_crs(3857, 4326, always_xy=True)
    w, s = t.transform(x0, y0)
    e, n = t.transform(x1, y1)
    return [[round(s, 6), round(w, 6)], [round(n, 6), round(e, 6)]]


def _write_png(rgba, path):
    Image.fromarray(rgba.astype("uint8"), "RGBA").save(path, optimize=True)


def categorical_png(da, colors, path):
    m = _to_mercator(da.astype("float32"), True)
    v = np.nan_to_num(m.values, nan=0).astype(int)
    rgba = np.zeros(v.shape + (4,), "uint8")
    for k, c in colors.items():
        rgba[v == k] = _hex(c) + [235]
    _write_png(rgba, path)
    return _bounds_latlon(m)


def continuous_png(da, cmap, vmin, vmax, path, log=False):
    m = _to_mercator(da.astype("float32"), False)
    v = m.values
    if log:
        v, vmin, vmax = np.log10(np.clip(v, 1e-3, None)), np.log10(vmin), np.log10(vmax)
    norm = np.clip((v - vmin) / (vmax - vmin), 0, 1)
    rgba = (colormaps[cmap](np.nan_to_num(norm)) * 255).astype("uint8")
    rgba[..., 3] = np.where(np.isfinite(v), 220, 0)
    _write_png(rgba, path)
    return _bounds_latlon(m)


def truecolour_png(s2, inside, path):
    import xarray as xr
    rgb = xr.concat([s2[b].where(inside) for b in ["B04", "B03", "B02"]], dim="band")
    rgb = rgb.rio.write_crs(s2["B04"].rio.crs)
    m = _to_mercator(rgb, False)
    a = m.values
    lo, hi = np.nanpercentile(a, 2), np.nanpercentile(a, 98)
    img = np.clip((a - lo) / (hi - lo), 0, 1)
    alpha = np.isfinite(a).all(axis=0)
    rgba = np.dstack([np.nan_to_num(img[i]) * 255 for i in range(3)] + [alpha * 255])
    _write_png(rgba, path)
    return _bounds_latlon(m)


def publish(cfg, r, layers, aoi_gdf, bbox, pub, maps_dir):
    pub = Path(pub)
    (pub / "layers").mkdir(parents=True, exist_ok=True)
    run_date = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    p0, p1 = cfg["period_baseline"], cfg["period_current"]
    s = r["stats"]

    # Map overlays
    lay = {}
    lay["truecolour"] = {"title": "Satellite image (current)", "file": "layers/truecolour.png",
                         "bounds": truecolour_png(r["state"]["current"]["s2"], r["inside"], pub / "layers/truecolour.png")}
    lay["risk"] = {"title": "Erosion risk class", "file": "layers/risk.png",
                   "bounds": categorical_png(r["risk"].fillna(0), RISK_COLORS, pub / "layers/risk.png"),
                   "legend": [[an.RISK_LABELS[k], c] for k, c in RISK_COLORS.items()]}
    lay["change"] = {"title": "Change since baseline", "file": "layers/change.png",
                     "bounds": categorical_png(r["change"], CHANGE_COLORS, pub / "layers/change.png"),
                     "legend": [[an.CHANGE_LABELS[k], c] for k, c in CHANGE_COLORS.items()]}
    if r.get("history") is not None:
        lay["history"] = {"title": "Historical change 1984-2021 (JRC)", "file": "layers/history.png",
                          "bounds": categorical_png(r["history"], HISTORY_COLORS, pub / "layers/history.png"),
                          "legend": [[an.HISTORY_LABELS[k], c] for k, c in HISTORY_COLORS.items()]}
    ml = r.get("ml")
    if ml is not None:
        import ml_model
        lay["ai"] = {"title": "AI bank-erosion susceptibility (next period)", "file": "layers/ai.png",
                     "bounds": categorical_png(ml["susceptibility_class"], SUSC_COLORS, pub / "layers/ai.png"),
                     "legend": [[ml_model.SUSC_LABELS[k], c] for k, c in SUSC_COLORS.items()]}
        lay["agreement"] = {"title": "AI model agreement (RF vs XGBoost)", "file": "layers/agreement.png",
                            "bounds": continuous_png(ml["agreement"], "viridis", 0.5, 1.0, pub / "layers/agreement.png"),
                            "ramp": {"cmap": "viridis", "labels": ["0.5 low", "0.75", "1.0 high"]}}
    if r.get("change_conf") is not None:
        lay["confidence"] = {"title": "Confidence of detected change", "file": "layers/confidence.png",
                             "bounds": continuous_png(r["change_conf"], "viridis", 0.5, 1.0,
                                                      pub / "layers/confidence.png"),
                             "ramp": {"cmap": "viridis", "labels": ["0.5 low", "0.75", "1.0 high"]}}
    lay["soilloss"] = {"title": "Soil loss (t/ha/yr)", "file": "layers/soilloss.png",
                       "bounds": continuous_png(r["A"], "YlOrRd", 0.1, 100, pub / "layers/soilloss.png", log=True),
                       "ramp": {"cmap": "YlOrRd", "labels": ["0.1", "1", "10", "100"]}}
    lay["ndvi"] = {"title": "NDVI (current)", "file": "layers/ndvi.png",
                   "bounds": continuous_png(r["state"]["current"]["ind"]["NDVI"], "RdYlGn", -0.2, 0.9,
                                            pub / "layers/ndvi.png"),
                   "ramp": {"cmap": "RdYlGn", "labels": ["-0.2", "0.35", "0.9"]}}
    (pub / "layers.json").write_text(json.dumps(lay, indent=1))

    # Vectors and tables
    if aoi_gdf is not None:
        aoi_gdf[["geometry"]].to_file(pub / "aoi.geojson", driver="GeoJSON")
    else:
        from shapely.geometry import box
        import geopandas as gpd
        gpd.GeoDataFrame(geometry=[box(*bbox)], crs="EPSG:4326").to_file(pub / "aoi.geojson", driver="GeoJSON")
    hot = r["hotspots"]
    if hot is not None:
        keep = [c for c in ["rank", "class", "source", "area_ha", "mean_soil_loss_t_ha_yr",
                            "max_retreat_m", "retreat_m_per_yr", "mean_probability", "model_agreement",
                            "confidence", "confidence_score", "population", "built_ha", "crop_ha",
                            "road_km", "embankment_km", "facilities", "nearest_place", "nearest_place_km",
                            "exposure_index", "hazard_score", "priority_score", "lat", "lon"]
                if c in hot.columns]
        h = hot.copy()
        h["geometry"] = h.geometry.simplify(cfg["resolution_m"])
        h[keep + ["geometry"]].to_crs("EPSG:4326").to_file(pub / "hotspots.geojson", driver="GeoJSON")
        hot[keep].to_csv(pub / "hotspots.csv", index=False)

    confident = s["clear_coverage_pct"] >= cfg.get("min_clear_coverage_pct", 60)
    summary = {"run_date": run_date, "district": cfg.get("aoi_district"),
               "period_baseline": p0, "period_current": p1,
               "resolution_m": cfg["resolution_m"], "confidence": "normal" if confident else "low",
               "repository": os.environ.get("GITHUB_REPOSITORY", ""), **s}
    rep = Path("data/inputs_report.json")
    if rep.exists():
        summary["inputs_report"] = json.loads(rep.read_text())
    (pub / "summary.json").write_text(json.dumps(summary, indent=2))

    row = {"run_date": run_date[:10], "current_start": p1[0], "current_end": p1[1],
           "baseline_start": p0[0], "baseline_end": p0[1],
           "clear_coverage_pct": s["clear_coverage_pct"], "confidence": summary["confidence"],
           "mean_soil_loss_t_ha_yr": s["mean_soil_loss_t_ha_yr"],
           **{f"risk_{k.lower().replace(' ', '_')}_ha": v for k, v in s["risk_class_area_ha"].items()},
           **{"chg_" + k.split(" (")[0].lower().replace(" ", "_") + "_ha": v for k, v in s["change_area_ha"].items()}}
    hp = pub / "history.csv"
    hist = pd.read_csv(hp) if hp.exists() else pd.DataFrame()
    if not hist.empty:
        hist = hist[hist["current_end"] != p1[1]]
    hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True).sort_values("current_end")
    hist.to_csv(hp, index=False)

    # Full-resolution GeoTIFFs for the GitHub release
    rel = Path(cfg["output_dir"]) / cfg["project_name"] / "release"
    shutil.rmtree(rel, ignore_errors=True)
    rel.mkdir(parents=True)
    r["risk"].fillna(0).astype("uint8").rio.write_nodata(0).rio.to_raster(rel / "erosion_risk_class.tif", compress="deflate")
    r["change"].rio.write_nodata(0).rio.to_raster(rel / "change_class.tif", compress="deflate")
    for tag in ["baseline", "current"]:
        f = r["state"][tag].get("freq")
        if f is not None:
            f.astype("float32").rio.write_crs(r["change"].rio.crs).rio.to_raster(
                rel / f"water_frequency_{tag}.tif", compress="deflate")
    if ml is not None:
        ml["probability"].astype("float32").rio.to_raster(rel / "ai_bank_erosion_probability.tif", compress="deflate")
        ml["agreement"].astype("float32").rio.to_raster(rel / "ai_model_agreement.tif", compress="deflate")
    if r.get("change_conf") is not None:
        r["change_conf"].astype("float32").rio.to_raster(rel / "change_confidence.tif", compress="deflate")
    if r.get("retreat") is not None:
        r["retreat"].astype("float32").rio.to_raster(rel / "bank_retreat_m.tif", compress="deflate")
    r["A"].astype("float32").rio.to_raster(rel / "soil_loss_t_ha_yr.tif", compress="deflate")
    for g, n in [(r["chg_gdf"], "change_patches"), (r["risk_gdf"], "high_risk_patches")]:
        if g is not None and not g.empty:
            g.to_crs("EPSG:4326").to_file(rel / f"{n}.geojson", driver="GeoJSON")
    for f in ["hotspots.csv", "summary.json"]:
        if (pub / f).exists():
            shutil.copy(pub / f, rel / f)
    (rel / "RUN_ID").write_text(p1[1])
    print(f"  published dashboard data to {pub} and release files to {rel}")
