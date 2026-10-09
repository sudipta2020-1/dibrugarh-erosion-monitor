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
        w, freq, info = an.water_from_frequency(ind, s1, c2, c1, cc)
        state[tag] = {"ind": ind, "water": w & inside, "freq": freq.where(inside), "s2": s2}
        water_info[tag] = info
        print(f"  water {tag}: {info}")

    coverage = float((np.isfinite(state["current"]["s2"]["B04"]) & inside).sum() / inside.sum() * 100)

    elev = al(layers["dem"]).where(inside)
    ru = an.rusle(state["current"]["ind"], elev, cfg, res, state["current"]["water"])
    A = ru["soil_loss_t_ha_yr"].where(inside).rio.write_crs(crs)
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

    mp = cc["min_patch_pixels"]
    chg_gdf = an.patches(chg, an.CHANGE_LABELS, mp, res, extra_max=retreat)
    if not chg_gdf.empty and "max_retreat_m" in chg_gdf:
        chg_gdf["retreat_m_per_yr"] = (chg_gdf["max_retreat_m"] / years).round(1)
    risk_gdf = an.patches(risk.where(risk >= 4, 0).rio.write_crs(crs), an.RISK_LABELS, mp, res, extra=A)
    hot = an.rank_hotspots(chg_gdf, risk_gdf, top_n=50)

    px_ha = res * res / 1e4
    stats = {
        "district_area_ha": round(float(inside.sum()) * px_ha, 1),
        "clear_coverage_pct": round(coverage, 1),
        "mean_soil_loss_t_ha_yr": round(float(A.mean(skipna=True)), 2),
        "risk_class_area_ha": {an.RISK_LABELS[k]: round(float((risk == k).sum()) * px_ha, 1) for k in range(1, 6)},
        "change_area_ha": {an.CHANGE_LABELS[k]: round(float((chg == k).sum()) * px_ha, 1) for k in an.CHANGE_LABELS},
        "years_between_periods": round(years, 2),
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
    if hist is not None:
        stats["history_area_ha"] = {an.HISTORY_LABELS[k]: round(float((hist == k).sum()) * px_ha, 1)
                                    for k in an.HISTORY_LABELS}
    # Five-class water-change map used for the accuracy assessment
    acc_map = tmpl.copy(data=va.map_classes(chg.values, state["baseline"]["water"].values,
                                            state["current"]["water"].values, inside.values))
    acc_map = acc_map.astype("uint8").rio.write_crs(crs)
    return {"inside": inside, "state": state, "A": A, "risk": risk, "change": chg,
            "acc_map": acc_map, "retreat": retreat,
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
