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

    state = {}
    for tag in ["baseline", "current"]:
        s2 = al(layers[tag]["s2"])
        s1 = al(layers[tag]["s1"]) if layers[tag]["s1"] is not None else None
        ind = an.indices(s2).where(inside)
        state[tag] = {"ind": ind, "water": an.water_mask(ind, s1, cc) & inside, "s2": s2}

    coverage = float((np.isfinite(state["current"]["s2"]["B04"]) & inside).sum() / inside.sum() * 100)

    elev = al(layers["dem"]).where(inside)
    ru = an.rusle(state["current"]["ind"], elev, cfg, res, state["current"]["water"])
    A = ru["soil_loss_t_ha_yr"].where(inside).rio.write_crs(crs)
    risk = an.classify(A, cfg["risk_thresholds"]).rio.write_crs(crs)
    chg, dndvi = an.change_detection(state["baseline"], state["current"], cc)
    chg = chg.where(inside, 0).astype("uint8").rio.write_crs(crs)

    mp = cc["min_patch_pixels"]
    chg_gdf = an.patches(chg, an.CHANGE_LABELS, mp, res)
    risk_gdf = an.patches(risk.where(risk >= 4, 0).rio.write_crs(crs), an.RISK_LABELS, mp, res, extra=A)
    hot = an.rank_hotspots(chg_gdf, risk_gdf, top_n=50)

    px_ha = res * res / 1e4
    stats = {
        "district_area_ha": round(float(inside.sum()) * px_ha, 1),
        "clear_coverage_pct": round(coverage, 1),
        "mean_soil_loss_t_ha_yr": round(float(A.mean(skipna=True)), 2),
        "risk_class_area_ha": {an.RISK_LABELS[k]: round(float((risk == k).sum()) * px_ha, 1) for k in range(1, 6)},
        "change_area_ha": {an.CHANGE_LABELS[k]: round(float((chg == k).sum()) * px_ha, 1) for k in range(1, 5)},
        "hotspots_listed": 0 if hot is None else int(len(hot)),
    }
    return {"inside": inside, "state": state, "A": A, "risk": risk, "change": chg,
            "dNDVI": dndvi.where(inside).rio.write_crs(crs), "chg_gdf": chg_gdf,
            "risk_gdf": risk_gdf, "hotspots": hot, "stats": stats}


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
    print(json.dumps(r["stats"], indent=2))

    if not args.skip_publish:
        print("== Publishing")
        publish(cfg, r, layers, aoi_gdf, bbox, Path(cfg["publish_dir"]), maps)
    print("Done.")


if __name__ == "__main__":
    main()
