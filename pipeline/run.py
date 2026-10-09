"""
Scheduled soil erosion monitoring job for Dibrugarh district.

  python pipeline/run.py                     # normal scheduled run
  python pipeline/run.py --end-date 2026-02-28
  python pipeline/run.py --skip-publish      # analysis only
"""
import argparse
import datetime as dt
import json
import shutil
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

import analysis as an
import exposure as exm
import ml_model
import validation as va
from acquire import acquire_all, get_aoi, save
from publish import publish


def resolve_periods(cfg, end_date):
    m = cfg.get("monitoring", {})
    if m.get("mode") != "rolling":
        return cfg["period_baseline"], cfg["period_current"]
    end = end_date
    start = end - dt.timedelta(days=int(m["window_days"]))
    shift = end.year - int(m["baseline_year"])

    def back(d):
        try:
            return d.replace(year=d.year - shift)
        except ValueError:          # 29 February
            return d.replace(year=d.year - shift, day=28)
    cur = [start.isoformat(), end.isoformat()]
    base = [back(start).isoformat(), back(end).isoformat()]
    return base, cur


def aoi_mask(template, gdf):
    if gdf is None:
        return xr.ones_like(template, dtype=bool)
    from rasterio.features import geometry_mask
    inside = geometry_mask(gdf.to_crs(template.rio.crs).geometry, out_shape=template.shape,
                           transform=template.rio.transform(), invert=True)
    return template.copy(data=inside).astype(bool)


def analyse(cfg, layers, aoi_gdf):
    res, cc, crs = cfg["resolution_m"], cfg["change"], cfg["crs"]
    tmpl = layers["current"]["s2"]["B04"]
    al = lambda o: o.assign_coords(x=tmpl.x, y=tmpl.y)
    inside = aoi_mask(tmpl, aoi_gdf)

    state, water_info = {}, {}
    for tag in ["baseline", "current"]:
        L = layers[tag]
        s2 = al(L["s2"])
        s1 = al(L["s1"]) if L["s1"] is not None else None
        c2 = al(L["s2_counts"]) if L.get("s2_counts") is not None else None
        c1 = al(L["s1_counts"]) if L.get("s1_counts") is not None else None
        ind = an.indices(s2).where(inside)
        w, freq, info, conf = an.water_from_frequency(ind, s1, c2, c1, cc)
        state[tag] = {"ind": ind, "water": w & inside, "freq": freq.where(inside), "s2": s2,
                      "conf": conf.where(inside)}
        water_info[tag] = info
        print(f"  water {tag}: {info}")

    coverage = float((np.isfinite(state["current"]["s2"]["B04"]) & inside).sum() / inside.sum() * 100)

    elev = al(layers["dem"]).where(inside)
    ru = an.rusle(state["current"]["ind"], elev, cfg, res, state["current"]["water"])
    A = ru["soil_loss_t_ha_yr"].where(inside).rio.write_crs(crs)
    # RUSLE with baseline vegetation, used as a baseline score for the ML comparison
    A0 = an.rusle(state["baseline"]["ind"], elev, cfg, res, state["baseline"]["water"])["soil_loss_t_ha_yr"]
    A0 = A0.where(inside).rio.write_crs(crs)
    risk = an.classify(A, cfg["risk_thresholds"]).rio.write_crs(crs)
    # Historical record 1984-2021 (JRC Global Surface Water), if prepared
    jrc = an.jrc_transitions(tmpl, cfg.get("history_raster"))
    hist = an.history_layer(tmpl, None, raw=jrc) if jrc is not None else None
    if hist is not None:
        hist = hist.where(inside, 0).astype("uint8").rio.write_crs(crs)

    chg, dndvi = an.change_detection(state["baseline"], state["current"], cc, jrc)
    chg = an.split_inland_water(chg, state["baseline"]["water"].values, res,
                                cc.get("river_min_area_ha", 50), cc.get("bank_touch_px", 2))
    chg = chg.where(inside, 0).astype("uint8").rio.write_crs(crs)

    # Bank retreat: distance from the baseline water edge to each eroded pixel
    retreat = an.retreat_distance(state["baseline"]["water"], res)
    retreat = retreat.where((chg == 1) | (chg == 5)).rio.write_crs(crs)
    years = max((dt.date.fromisoformat(cfg["period_current"][1]) -
                 dt.date.fromisoformat(cfg["period_baseline"][1])).days / 365.25, 1e-6)

    # Confidence of each water-change pixel: both period labels must be right
    chg_conf = (state["baseline"]["conf"] * state["current"]["conf"]).astype("float32")
    chg_conf = chg_conf.where(np.isin(chg.values, [1, 2, 5, 6])).rio.write_crs(crs).rename("change_confidence")

    mp = cc["min_patch_pixels"]
    chg_gdf = an.patches(chg, an.CHANGE_LABELS, mp, res, extra_max=retreat)
    if not chg_gdf.empty and "max_retreat_m" in chg_gdf:
        chg_gdf["retreat_m_per_yr"] = (chg_gdf["max_retreat_m"] / years).round(1)
    if not chg_gdf.empty:
        chg_gdf["confidence_score"] = np.round(an.zonal_mean(chg_gdf, chg_conf), 3)
    risk_gdf = an.patches(risk.where(risk >= 4, 0).rio.write_crs(crs), an.RISK_LABELS, mp, res, extra=A)

    # Machine-learning bank-erosion susceptibility (RF + XGBoost), compared with RUSLE
    print("== Machine-learning model")
    ml_in = {"inside": inside, "state": state, "change": chg, "K": ru["K"].where(inside),
             "A_baseline": A0, "jrc": jrc}
    try:
        ml = ml_model.run(cfg, ml_in, {"dem_aligned": elev.values}, res)
    except Exception as e:                       # the monitoring run must not fail because of the model
        print(f"  ML model skipped: {e}")
        ml = None
    ai_gdf = None
    if ml is not None:
        for k in ["probability", "agreement", "susceptibility_class"]:
            ml[k] = ml[k].rio.write_crs(crs)
        # High and very high zones, cut into 1 km tiles so each site is a visitable unit
        hi = ml["susceptibility_class"].values >= 4
        k = max(1, int(round(cfg.get("ml", {}).get("site_tile_m", 1000) / res)))
        rr_, cc_ = np.indices(hi.shape)
        tiles = np.where(hi, (rr_ // k) * (hi.shape[1] // k + 1) + cc_ // k + 1, 0).astype("int32")
        ai_gdf = an.patches(tmpl.copy(data=tiles).rio.write_crs(crs),
                            {int(t): an.AI_LABEL for t in np.unique(tiles) if t > 0}, mp, res)
        if not ai_gdf.empty:
            ai_gdf["mean_probability"] = np.round(an.zonal_mean(ai_gdf, ml["probability"]), 3)
            ai_gdf["model_agreement"] = np.round(an.zonal_mean(ai_gdf, ml["agreement"]), 3)
            auc = ml["summary"]["metrics"]["Ensemble (RF + XGBoost)"]["roc_auc"]
            ai_gdf["model_skill"] = round(float(np.clip((auc - 0.5) / 0.4, 0, 1)), 3)

    # Exposure of people and assets, and the final priority list
    ex = exm.load(crs)
    print("  exposure layers available:", exm.available(ex) or "none")
    hot = an.rank_hotspots(chg_gdf, risk_gdf, ai_gdf, exposure=ex, cfg=cfg, top_n=50)

    px_ha = res * res / 1e4
    stats = {
        "district_area_ha": round(float(inside.sum()) * px_ha, 1),
        "clear_coverage_pct": round(coverage, 1),
        "mean_soil_loss_t_ha_yr": round(float(A.mean(skipna=True)), 2),
        "risk_class_area_ha": {an.RISK_LABELS[k]: round(float((risk == k).sum()) * px_ha, 1) for k in range(1, 6)},
        "change_area_ha": {an.CHANGE_LABELS[k]: round(float((chg == k).sum()) * px_ha, 1) for k in an.CHANGE_LABELS},
        "years_between_periods": round(years, 2),
        "confidence_summary": {
            "mean_change_confidence": round(float(np.nanmean(chg_conf.values)), 3)
            if np.isfinite(chg_conf.values).any() else None,
            "change_area_high_confidence_ha": round(float((chg_conf.values >= 0.9).sum()) * res * res / 1e4, 1),
            "method": "Beta posterior of per-date water observations; change confidence = product of "
                      "the baseline and current label confidences"},
        "water_detection": {**water_info, "char_split": "JRC 1984-2021 water history" if jrc is not None
                            else "not applied (JRC layer missing)"},
        "hotspots_listed": 0 if hot is None else int(len(hot)),
        "rusle_inputs": {
            "rainfall": "IMD gridded mean annual rainfall" if ru.attrs["rainfall_from_file"]
                        else f"uniform {cfg['rusle']['annual_rainfall_mm']} mm (placeholder)",
            "soil_k": "SoilGrids topsoil texture, Williams EPIC equation" if ru.attrs["k_from_file"]
                      else f"uniform K = {cfg['rusle']['k_factor']} (placeholder)",
            "mean_rainfall_mm": round(float(ru["P_mm"].where(inside).mean()), 0),
            "mean_R": round(float(ru["R"].where(inside).mean()), 1),
            "mean_K": round(float(ru["K"].where(inside).mean()), 4),
            "dem_smoothing_m": cfg["rusle"].get("dem_smooth_m", 0),
        },
    }
    if ml is not None:
        stats["ml_model"] = ml["summary"]
    exp_avail = exm.available(ex)
    stats["exposure"] = {"layers": exp_avail, "buffer_m": cfg.get("exposure", {}).get("buffer_m", 500)}
    if "population" in exp_avail:
        b = cfg.get("exposure", {}).get("buffer_m", 500)
        stats["exposure"]["people_near_bank_erosion"] = exm.people_near((chg == 1).astype("uint8"), ex, b)
        if ml is not None:
            stats["exposure"]["people_near_high_ai_susceptibility"] = exm.people_near(
                (ml["susceptibility_class"] >= 5).astype("uint8"), ex, b)
    if hist is not None:
        stats["history_area_ha"] = {an.HISTORY_LABELS[k]: round(float((hist == k).sum()) * px_ha, 1)
                                    for k in an.HISTORY_LABELS}
    # Five-class water-change map used for the accuracy assessment
    acc_map = tmpl.copy(data=va.map_classes(chg.values, state["baseline"]["water"].values,
                                            state["current"]["water"].values, inside.values))
    acc_map = acc_map.astype("uint8").rio.write_crs(crs)
    return {"inside": inside, "state": state, "A": A, "risk": risk, "change": chg,
            "acc_map": acc_map, "retreat": retreat, "change_conf": chg_conf, "ml": ml,
            "dNDVI": dndvi.where(inside).rio.write_crs(crs), "chg_gdf": chg_gdf,
            "risk_gdf": risk_gdf, "hotspots": hot, "stats": stats, "history": hist}


def validate(cfg, r, layers):
    """Draw the validation sample once; assess the map when labels exist for the
    same monitoring periods."""
    v = cfg.get("validation", {})
    if not v.get("enabled", True):
        return
    vdir = Path(cfg["publish_dir"]) / "validation"
    periods = [cfg["period_baseline"], cfg["period_current"]]
    labels = Path(v.get("labels", "data/validation_labels.csv"))
    old = (vdir / "sample_meta.json").exists() and \
        json.loads((vdir / "sample_meta.json").read_text()).get("sample_version", 1) < va.SAMPLE_VERSION
    # Draw the sample once. Redraw only when the class scheme has changed and no
    # labels have been collected yet, so labelled work is never thrown away.
    if not (vdir / "sample_meta.json").exists() or (old and not labels.exists()):
        va.create(vdir, r["acc_map"].values, r["acc_map"], r["state"]["baseline"]["s2"],
                  r["state"]["current"]["s2"], v, periods, cfg["resolution_m"])
    meta = json.loads((vdir / "sample_meta.json").read_text())
    r["stats"]["validation"] = {"sample_points": meta["points"],
                                "period_baseline": meta["period_baseline"],
                                "period_current": meta["period_current"]}
    same = [meta["period_baseline"], meta["period_current"]] == periods
    px_ha = cfg["resolution_m"] ** 2 / 1e4
    res = va.assess(vdir, labels, r["acc_map"], px_ha) if same else None
    if res is not None:
        (vdir / "accuracy.json").write_text(json.dumps(res, indent=2))
        r["stats"]["accuracy"] = res
        print(f"  accuracy: {res.get('overall_accuracy')} +/- {res.get('overall_ci95')} "
              f"({res.get('labelled')} labelled points)")
    elif (vdir / "accuracy.json").exists():
        # Keep the latest assessment visible, marked with the periods it refers to
        r["stats"]["accuracy"] = json.loads((vdir / "accuracy.json").read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--end-date", default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--skip-publish", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    end = dt.date.fromisoformat(args.end_date) if args.end_date else dt.date.today()
    cfg["period_baseline"], cfg["period_current"] = resolve_periods(cfg, end)
    run_id = cfg["period_current"][1]
    print(f"Run {run_id}: baseline {cfg['period_baseline']}  current {cfg['period_current']}")

    work = Path(cfg["output_dir"]) / cfg["project_name"]
    raw = work / "raw" / run_id
    # Keep only this run's composites in the cached raw folder.
    if raw.parent.exists():
        for old in raw.parent.iterdir():
            if old.is_dir() and old.name != run_id:
                shutil.rmtree(old, ignore_errors=True)
    maps = work / "maps"
    maps.mkdir(parents=True, exist_ok=True)

    print("== Area of interest")
    aoi_gdf, bbox = get_aoi(cfg)
    print("== Satellite data")
    layers = acquire_all(cfg, raw, bbox)
    print("== Analysis")
    r = analyse(cfg, layers, aoi_gdf)

    for name in ["A", "risk", "change", "dNDVI"]:
        save(r[name], maps / f"{name}.tif")

    print("== Accuracy assessment")
    validate(cfg, r, layers)
    print(json.dumps(r["stats"], indent=2))

    if not args.skip_publish:
        print("== Publishing")
        publish(cfg, r, layers, aoi_gdf, bbox, Path(cfg["publish_dir"]), maps)
    print("Done.")


if __name__ == "__main__":
    main()
