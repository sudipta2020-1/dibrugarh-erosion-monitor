"""
SWiFT-Bank edition of the district monitoring system.

Builds the same dashboard as the standard system (map layers, statistics, priority
sites with urgency and recommended measures, field form, sensors, run history),
but the water change in the river corridor comes from SWiFT-Bank: the tested
sub-pixel water-fraction method at 10 m. Outside the corridor, and for vegetation
loss, bare soil, RUSLE risk and the AI model, the results of the standard run are
used, because SWiFT-Bank only maps water change.

Called at the end of pipeline/swift.py (comparison step). Writes docs/swift/data/.
"""
import datetime as dt
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import rioxarray  # noqa: F401
from rasterio.enums import Resampling
from scipy import stats

import analysis as an
import exposure as exm
import field
import interventions
import publish as pb

WATER_CLASSES = [1, 2, 5, 6]
KEEP = ["rank", "site_id", "class", "source", "urgency", "lead_agency", "recommended_action",
        "field_status", "field_date", "field_visits", "area_ha", "footprint_ha", "p_value",
        "mean_soil_loss_t_ha_yr", "mean_slope_deg", "max_retreat_m", "retreat_m_per_yr",
        "mean_probability", "model_agreement", "confidence", "confidence_score", "population",
        "built_ha", "crop_ha", "road_km", "embankment_km", "facilities", "nearest_place",
        "nearest_place_km", "exposure_index", "hazard_score", "priority_score", "lat", "lon"]


def _release(repo, name, dest):
    r = requests.get(f"https://github.com/{repo}/releases/latest/download/{name}", timeout=180)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def _latlon(gdf):
    p = gdf.geometry.representative_point().to_crs("EPSG:4326")
    gdf["lat"], gdf["lon"] = p.y.round(6), p.x.round(6)
    return gdf


def _to10(da20, tmpl10, resampling):
    return da20.rio.reproject_match(tmpl10, resampling=resampling).values


def _to20(arr10, tmpl10, grid20, resampling, nodata):
    da = tmpl10.copy(data=arr10).rio.write_nodata(nodata)
    return da.rio.reproject_match(grid20, resampling=resampling).values


def build(cfg, ctx):
    """ctx: results of the SWiFT comparison step (see swift.main)."""
    print("== SWiFT-Bank dashboard")
    crs = cfg["crs"]
    pub_std = Path(cfg["publish_dir"])
    out = pub_std.parent / "swift" / "data"
    (out / "layers").mkdir(parents=True, exist_ok=True)
    S0 = json.loads((pub_std / "summary.json").read_text())
    repo = S0.get("repository", "")
    raw = ctx["raw"]
    c10, d10, z10, tmpl10, inside10 = ctx["c"], ctx["d"], ctx["z"], ctx["tmpl"], ctx["inside"]
    P = ctx["P"]
    years = float(S0.get("years_between_periods") or 6.0)

    # ---------- standard 20 m layers from the latest release
    c20 = rioxarray.open_rasterio(_release(repo, "change_class.tif", raw / "std_change_class.tif")).squeeze("band", drop=True)
    try:
        conf20 = rioxarray.open_rasterio(_release(repo, "change_confidence.tif", raw / "std_change_conf.tif"),
                                         masked=True).squeeze("band", drop=True)
        conf20 = conf20.rio.reproject_match(c20).values
    except Exception:
        conf20 = np.full(c20.shape, np.nan, "float32")
    from rasterio.features import geometry_mask
    aoi = ctx["aoi"]
    inside20 = ~geometry_mask(aoi.to_crs(crs).geometry, out_shape=c20.shape, transform=c20.rio.transform())
    corr20 = ctx["cmask"].rio.reproject_match(c20, resampling=Resampling.nearest).values.astype(bool) & inside20
    px20 = abs(c20.rio.resolution()[0]) ** 2 / 1e4
    px10 = 0.01

    # ---------- merged change map (20 m grid): SWiFT in the corridor, standard elsewhere
    m = np.nan_to_num(c20.values, nan=0).astype("uint8")
    m[corr20 & np.isin(m, WATER_CLASSES)] = 0
    for k in [2, 6, 5, 1]:                          # loss classes last, so they win in mixed cells
        mk = _to20((c10 == k).astype("uint8"), tmpl10, c20, Resampling.max, 0)
        m[(mk == 1) & inside20] = k
    merged = c20.copy(data=m).astype("uint8").rio.write_crs(crs)

    # Confidence of each change: 1 - p of the pixel test (SWiFT), Beta posterior elsewhere
    with np.errstate(invalid="ignore"):
        conf10 = np.where(np.isin(c10, [1, 5, 6]), stats.norm.cdf(z10),
                          np.where(c10 == 2, stats.norm.cdf(-z10), np.nan)).astype("float32")
    conf10_20 = _to20(conf10, tmpl10, c20, Resampling.average, np.nan)
    confm = np.where(corr20, conf10_20, conf20).astype("float32")
    confm = np.where(np.isin(m, [1, 2, 5, 6]) & inside20, confm, np.nan)
    confm_da = c20.copy(data=confm).astype("float32").rio.write_crs(crs)

    # ---------- layers: copy the standard overlays, replace change and confidence
    lay = json.loads((pub_std / "layers.json").read_text())
    for k, v in lay.items():
        if k in ("change", "confidence"):
            continue
        src = pub_std / v["file"]
        if src.exists():
            shutil.copy(src, out / v["file"])
    lay["change"] = {"title": "Change since baseline (SWiFT-Bank in the river corridor)", "file": "layers/change.png",
                     "bounds": pb.categorical_png(merged, pb.CHANGE_COLORS, out / "layers/change.png"),
                     "legend": [[an.CHANGE_LABELS[k], c] for k, c in pb.CHANGE_COLORS.items()]}
    lay["confidence"] = {"title": "Confidence of detected change (SWiFT test)", "file": "layers/confidence.png",
                         "bounds": pb.continuous_png(confm_da, "viridis", 0.5, 1.0, out / "layers/confidence.png"),
                         "ramp": {"cmap": "viridis", "labels": ["0.5 low", "0.75", "1.0 high"]}}
    (out / "layers.json").write_text(json.dumps(lay, indent=1))
    for f in ["aoi.geojson"]:
        if (pub_std / f).exists():
            shutil.copy(pub_std / f, out / f)
    if (pub_std / "sensors").exists():
        shutil.copytree(pub_std / "sensors", out / "sensors", dirs_exist_ok=True)

    # ---------- candidate sites
    frames = []
    if len(P):
        g = P.copy()
        g["class_id"] = [5 if c == an.CHANGE_LABELS[5] else 1 for c in g["class"]]
        g["area_ha"] = g["loss_ha"]
        g["max_retreat_m"] = np.round(g["retreat_subpixel_m"].astype(float), 0)
        g["retreat_m_per_yr"] = np.round(g["retreat_subpixel_m"].astype(float) / years, 1)
        g["confidence_score"] = np.round(1 - g["p_value"].astype(float), 3)
        frames.append(_latlon(g))
    acc = an.patches(tmpl10.copy(data=np.where(c10 == 2, 2, 0).astype("uint8")), an.CHANGE_LABELS, 5, 10)
    if not acc.empty:
        fp = acc["area_ha"].values
        acc["footprint_ha"] = fp
        acc["area_ha"] = np.round(an.zonal_mean(acc, tmpl10.copy(data=np.abs(d10).astype("float32"))) * fp, 3)
        acc["confidence_score"] = np.round(an.zonal_mean(acc, tmpl10.copy(data=conf10)), 3)
        frames.append(acc)
    std_rel = raw / "std_change_patches.geojson"
    try:
        sp = __import__("geopandas").read_file(_release(repo, "change_patches.geojson", std_rel)).to_crs(crs)
        veg = sp[sp["class"].isin([an.CHANGE_LABELS[3], an.CHANGE_LABELS[4]])]
        if len(veg):
            frames.append(veg)
    except Exception as e:
        print("  standard change patches not read:", e)
    import geopandas as gpd
    chg_gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=crs) if frames else None
    try:
        risk_gdf = gpd.read_file(_release(repo, "high_risk_patches.geojson", raw / "std_risk.geojson")).to_crs(crs)
    except Exception:
        risk_gdf = None
    hs = gpd.read_file(pub_std / "hotspots.geojson").to_crs(crs)
    ai_gdf = hs[hs["class"] == an.AI_LABEL].copy()
    try:
        auc = S0["ml_model"]["metrics"]["Ensemble (RF + XGBoost)"]["roc_auc"]
        ai_gdf["model_skill"] = round(float(np.clip((auc - 0.5) / 0.4, 0, 1)), 3)
    except Exception:
        ai_gdf["model_skill"] = 0.0
    ai_gdf = ai_gdf[[c for c in ai_gdf.columns if c in ("class", "area_ha", "mean_probability", "model_agreement",
                                                        "model_skill", "lat", "lon", "geometry")]]

    ex = exm.load(crs)
    hot = an.rank_hotspots(chg_gdf, risk_gdf, ai_gdf, exposure=ex, cfg=cfg, top_n=50)
    run_end = S0["period_current"][1]
    field_summary, field_pts = None, None
    if hot is not None:
        hot = interventions.apply(hot, cfg)
        fc = cfg.get("field", {})
        hot, field_summary, field_pts = field.ingest(fc.get("records", "data/field_records.csv"), hot, run_end, crs,
                                                     fc.get("match_m", 300))
        hot["site_id"] = ["S" + field.site_id(run_end, k) for k in hot["rank"]]
        keep = [c for c in KEEP if c in hot.columns]
        h = hot.copy()
        h["geometry"] = h.geometry.simplify(10)
        h[keep + ["geometry"]].to_crs("EPSG:4326").to_file(out / "hotspots.geojson", driver="GeoJSON")
        hot[keep].to_csv(out / "hotspots.csv", index=False)
        fdir = out / "field"
        field.write_sites(hot, run_end, fdir)
        sites = pd.read_csv(fdir / field.SITES_FILE)
        sites["name"] = [("S" + n) if n != "NEW" else n for n in sites["name"].astype(str)]
        sites.to_csv(fdir / field.SITES_FILE, index=False)
        field.write_form(fdir, version=dt.datetime.utcnow().strftime("%Y%m%d%H"))

    # ---------- statistics
    s = {k: v for k, v in S0.items() if k not in ("validation", "accuracy", "inputs_report")}
    s["run_date"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    area = {}
    for k, lab in an.CHANGE_LABELS.items():
        if k in WATER_CLASSES:
            a10 = float(np.abs(d10[c10 == k]).sum() * px10)
            a20 = float(((np.nan_to_num(c20.values) == k) & ~corr20 & inside20).sum() * px20)
            area[lab] = round(a10 + a20, 1)
        else:
            area[lab] = S0["change_area_ha"].get(lab, 0.0)
    s["change_area_ha"] = area
    cv = conf10[np.isfinite(conf10)]
    s["confidence_summary"] = {
        "mean_change_confidence": round(float(cv.mean()), 3) if cv.size else None,
        "change_area_high_confidence_ha": round(float((conf10 >= 0.9).sum() * px10), 1),
        "method": "SWiFT-Bank: one minus the p-value of the per-pixel Welch test (river corridor); "
                  "Beta posterior of the standard run elsewhere"}
    s["hotspots_listed"] = 0 if hot is None else int(len(hot))
    s["sites_by_urgency"] = interventions.summary(hot)
    s["field"] = field_summary or {"records": 0}
    b = cfg.get("exposure", {}).get("buffer_m", 500)
    if exm.available(ex) and "population" in exm.available(ex):
        s.setdefault("exposure", {})["people_near_bank_erosion"] = exm.people_near(
            merged.copy(data=(merged.values == 1).astype("uint8")), ex, b)
    sw = ctx["out"]
    s["method"] = "SWiFT-Bank"
    s["swift"] = {"corridor_km2": sw.get("area_analysed_km2"), "min_patch_ha": sw.get("min_patch_ha"),
                  "small_patches": sw.get("small_patches"), "small_ha": sw.get("small_ha"),
                  "null_test": sw.get("null_test"), "mdc_bank_m2": sw.get("mdc_bank_m2"),
                  "bank_loss_patches": sw.get("bank_loss_patches")}
    s["accuracy_note"] = ("The accuracy assessment sample was drawn for the standard map and is reported on the "
                          "standard district tab. For SWiFT-Bank, the false-alarm rate is checked on every run with "
                          "the split-window null test: " + str((sw.get("null_test") or {}).get("swift_footprint_ha"))
                          + " ha of false change in this run. Field checks at the reported small losses are needed "
                          "to measure its accuracy against the ground.")
    s["validate_url"] = "../validate.html"
    (out / "summary.json").write_text(json.dumps(s, indent=2))

    # ---------- run history
    p0, p1 = s["period_baseline"], s["period_current"]
    row = {"run_date": s["run_date"][:10], "current_start": p1[0], "current_end": p1[1],
           "baseline_start": p0[0], "baseline_end": p0[1],
           "clear_coverage_pct": s.get("clear_coverage_pct"), "confidence": s.get("confidence", "normal"),
           "mean_soil_loss_t_ha_yr": s.get("mean_soil_loss_t_ha_yr"),
           **{f"risk_{k.lower().replace(' ', '_')}_ha": v for k, v in s["risk_class_area_ha"].items()},
           **{"chg_" + k.split(" (")[0].lower().replace(" ", "_") + "_ha": v for k, v in area.items()}}
    hp = out / "history.csv"
    hist = pd.read_csv(hp) if hp.exists() else pd.DataFrame()
    if not hist.empty:
        hist = hist[hist["current_end"] != p1[1]]
    pd.concat([hist, pd.DataFrame([row])], ignore_index=True).sort_values("current_end").to_csv(hp, index=False)
    print(f"  SWiFT dashboard: {s['hotspots_listed']} sites, bank erosion {area[an.CHANGE_LABELS[1]]} ha, "
          f"char loss {area[an.CHANGE_LABELS[5]]} ha; written to {out}")
