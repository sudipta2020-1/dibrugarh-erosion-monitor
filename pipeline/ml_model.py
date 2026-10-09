"""
Machine-learning susceptibility model for riverbank erosion.

Design
------
The model learns from bank loss that has already been observed by satellite.
Conditions at the start of the comparison (the baseline window, 2020) are the
predictors, and the label is whether the land had been lost to the river by the
current window (2026). Using conditions from before the change avoids the
model "seeing" the answer. The fitted model is then applied to the current
conditions to estimate where bank loss is most likely over a period of similar
length (about six years).

Population: land pixels inside the district within `max_distance_m` of a river
channel (connected water body >= river_min_area_ha) at the time of prediction.
Label 1: change classes 1 (bank erosion of stable land) and 5 (char or sandbar
lost). New inland water (class 6) is not bank erosion and is labelled 0.

Models: Random Forest and XGBoost, and their mean (ensemble).
Baselines scored on the same points: RUSLE soil loss (baseline window) and
distance to the river channel alone.

Validation: spatial block cross-validation. The study area is cut into blocks
along the river, and each block is predicted by a model trained on the others,
so neighbouring pixels never sit on both sides of the split (Roberts et al.,
2017). Reported: ROC AUC, average precision (PR AUC), Brier score, and the
share of eroded land captured in the 10% and 20% of land ranked highest.
"""
import numpy as np
import xarray as xr
from scipy.ndimage import distance_transform_edt, minimum_filter, uniform_filter

import analysis as an

FEATURES = {
    "dist_river_m": "Distance to channel",
    "river_frac_300m": "River water within 300 m",
    "river_frac_1km": "River water within 1 km",
    "river_frac_3km": "River water within 3 km",
    "height_above_low_m": "Height above low ground",
    "slope_deg": "Slope",
    "ndvi": "NDVI",
    "ndvi_300m": "NDVI within 300 m",
    "bsi": "Bare soil index",
    "mndwi": "MNDWI",
    "water_freq": "Water frequency",
    "k_factor": "Soil erodibility K",
    "jrc_ever_water": "Water at any time 1984-2021",
    "jrc_loss_frac_1km": "Past bank loss within 1 km",
}
SUSC_THRESHOLDS = [0.05, 0.15, 0.30, 0.50]     # probability edges of the 5 classes
SUSC_LABELS = {1: "Very low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very high"}


def _mean_filter(a, size):
    """Moving average that ignores NaN."""
    a = np.asarray(a, dtype="float32")
    ok = np.isfinite(a)
    num = uniform_filter(np.where(ok, a, 0).astype("float32"), size=size, mode="nearest")
    den = uniform_filter(ok.astype("float32"), size=size, mode="nearest")
    return np.where(den > 0, num / np.maximum(den, 1e-6), np.nan).astype("float32")


def _px(m, res):
    return max(1, int(round(m / res)) | 1)


def static_features(elev, k, jrc, res, smooth_m=100):
    """Features that do not change between the two periods."""
    z = _mean_filter(np.asarray(elev), _px(smooth_m, res))
    zf = np.where(np.isfinite(z), z, np.nanmax(z) if np.isfinite(z).any() else 0)
    low = minimum_filter(zf, size=_px(1000, res), mode="nearest")
    dzdy, dzdx = np.gradient(zf, res, res)
    f = {"height_above_low_m": (zf - low).astype("float32"),
         "slope_deg": np.degrees(np.arctan(np.hypot(dzdx, dzdy))).astype("float32"),
         "k_factor": np.asarray(k, dtype="float32")}
    if jrc is not None:
        j = np.asarray(jrc)
        f["jrc_ever_water"] = ((j >= 1) & (j <= 10)).astype("float32")
        f["jrc_loss_frac_1km"] = uniform_filter(np.isin(j, [2, 5]).astype("float32"),
                                                size=_px(1000, res), mode="nearest")
    else:
        f["jrc_ever_water"] = np.zeros_like(f["slope_deg"])
        f["jrc_loss_frac_1km"] = np.zeros_like(f["slope_deg"])
    return f


def period_features(state, res, min_river_ha):
    """Features that describe one period (baseline or current)."""
    river = an.river_mask(state["water"].values, res, min_river_ha)
    rf = river.astype("float32")
    ind = state["ind"]
    ndvi = np.asarray(ind["NDVI"], dtype="float32")
    return river, {
        "dist_river_m": (distance_transform_edt(~river) * res).astype("float32") if river.any()
        else np.full(river.shape, 1e5, "float32"),
        "river_frac_300m": uniform_filter(rf, size=_px(300, res), mode="nearest"),
        "river_frac_1km": uniform_filter(rf, size=_px(1000, res), mode="nearest"),
        "river_frac_3km": uniform_filter(rf, size=_px(3000, res), mode="nearest"),
        "ndvi": ndvi,
        "ndvi_300m": _mean_filter(ndvi, _px(300, res)),
        "bsi": np.asarray(ind["BSI"], dtype="float32"),
        "mndwi": np.asarray(ind["MNDWI"], dtype="float32"),
        "water_freq": np.nan_to_num(np.asarray(state["freq"], dtype="float32"), nan=0.0),
    }


def _matrix(feats, mask):
    return np.column_stack([np.nan_to_num(feats[k][mask], nan=0.0) for k in FEATURES]).astype("float32")


def _blocks(rows, cols, n_blocks):
    """Blocks along the main axis of the sampled area (the river runs SW-NE)."""
    xy = np.column_stack([cols, -rows]).astype("float64")
    xy -= xy.mean(axis=0)
    _, _, vt = np.linalg.svd(xy[:: max(1, len(xy) // 20000)], full_matrices=False)
    along = xy @ vt[0]
    edges = np.quantile(along, np.linspace(0, 1, n_blocks + 1)[1:-1])
    return np.digitize(along, edges)


def _metrics(y, s):
    from sklearn.metrics import average_precision_score, roc_auc_score
    s = np.asarray(s, dtype="float64")
    order = np.argsort(-s)
    pos = y.sum()
    out = {"roc_auc": round(float(roc_auc_score(y, s)), 3),
           "pr_auc": round(float(average_precision_score(y, s)), 3)}
    for q in (10, 20):
        top = order[: int(len(s) * q / 100)]
        out[f"captured_top{q}pct"] = round(float(y[top].sum() / pos), 3) if pos else None
    return out


def _models(seed):
    from sklearn.ensemble import RandomForestClassifier
    from xgboost import XGBClassifier
    rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=20, max_features="sqrt",
                                n_jobs=-1, random_state=seed)
    xgb = XGBClassifier(n_estimators=400, max_depth=6, learning_rate=0.05, subsample=0.8,
                        colsample_bytree=0.8, min_child_weight=5, tree_method="hist",
                        eval_metric="logloss", n_jobs=-1, random_state=seed)
    return {"Random Forest": rf, "XGBoost": xgb}


def run(cfg, r, layers, res):
    """Train, cross-validate and apply the model. Returns a dict with maps and metrics,
    or None when there is too little observed change to learn from."""
    from sklearn.metrics import brier_score_loss
    mc = cfg.get("ml", {})
    if not mc.get("enabled", True):
        return None
    seed = int(mc.get("seed", 42))
    max_d = float(mc.get("max_distance_m", 3000))
    min_river = float(cfg["change"].get("river_min_area_ha", 50))
    rng = np.random.default_rng(seed)
    inside = r["inside"].values
    tmpl = r["change"]

    elev = layers["dem_aligned"]
    k_map = r["K"]
    stat = static_features(elev, k_map, r.get("jrc"), res, cfg["rusle"].get("dem_smooth_m", 100))
    river0, f0 = period_features(r["state"]["baseline"], res, min_river)
    river1, f1 = period_features(r["state"]["current"], res, min_river)
    f0.update(stat)
    f1.update(stat)

    land0 = ~r["state"]["baseline"]["water"].values
    dom0 = inside & land0 & (f0["dist_river_m"] <= max_d) & np.isfinite(f0["ndvi"])
    chg = np.asarray(r["change"].values)
    y_all = np.isin(chg, [1, 5]) & dom0
    rows, cols = np.nonzero(dom0)
    n_dom, n_pos = rows.size, int(y_all.sum())
    print(f"  ML training domain: {n_dom} pixels, {n_pos} with bank loss")
    if n_pos < 200 or n_dom < 2000:
        print("  too little observed bank loss to train a model")
        return None

    n_s = min(int(mc.get("sample_size", 150000)), n_dom)
    pick = rng.choice(n_dom, size=n_s, replace=False)
    rr, cc_ = rows[pick], cols[pick]
    sel = np.zeros_like(dom0)
    sel[rr, cc_] = True
    X = np.column_stack([np.nan_to_num(f0[k][rr, cc_], nan=0.0) for k in FEATURES]).astype("float32")
    y = y_all[rr, cc_].astype("int8")
    groups = _blocks(rr, cc_, int(mc.get("cv_blocks", 6)))

    # Baseline scores on the same points
    rusle0 = np.nan_to_num(np.asarray(r["A_baseline"].values)[rr, cc_], nan=0.0)
    dist0 = f0["dist_river_m"][rr, cc_]

    from sklearn.model_selection import GroupKFold
    oof = {name: np.zeros(len(y)) for name in ["Random Forest", "XGBoost"]}
    for tr, te in GroupKFold(n_splits=len(np.unique(groups))).split(X, y, groups):
        if y[tr].sum() == 0:
            continue
        for name, m in _models(seed).items():
            m.fit(X[tr], y[tr])
            oof[name][te] = m.predict_proba(X[te])[:, 1]
    oof["Ensemble (RF + XGBoost)"] = (oof["Random Forest"] + oof["XGBoost"]) / 2

    metrics = {}
    for name, s in oof.items():
        metrics[name] = {**_metrics(y, s), "brier": round(float(brier_score_loss(y, s)), 4)}
    metrics["RUSLE soil loss (baseline)"] = _metrics(y, rusle0)
    metrics["Distance to river only"] = _metrics(y, -dist0)

    # Final models on all sampled points, applied to current conditions
    final = _models(seed)
    for m in final.values():
        m.fit(X, y)
    imp_rf = final["Random Forest"].feature_importances_
    imp_xgb = final["XGBoost"].feature_importances_
    imp = (imp_rf / imp_rf.sum() + imp_xgb / max(imp_xgb.sum(), 1e-9)) / 2
    importance = sorted(((FEATURES[k], round(float(v), 4)) for k, v in zip(FEATURES, imp)),
                        key=lambda t: -t[1])

    land1 = ~r["state"]["current"]["water"].values
    dom1 = inside & land1 & (f1["dist_river_m"] <= max_d) & np.isfinite(f1["ndvi"])
    X1 = _matrix(f1, dom1)
    p_rf, p_xgb = np.empty(len(X1), "float32"), np.empty(len(X1), "float32")
    for i in range(0, len(X1), 400000):
        p_rf[i:i + 400000] = final["Random Forest"].predict_proba(X1[i:i + 400000])[:, 1]
        p_xgb[i:i + 400000] = final["XGBoost"].predict_proba(X1[i:i + 400000])[:, 1]
    prob = np.full(dom1.shape, np.nan, "float32")
    agree = np.full(dom1.shape, np.nan, "float32")
    prob[dom1] = (p_rf + p_xgb) / 2
    agree[dom1] = 1 - np.abs(p_rf - p_xgb)

    edges = [-np.inf] + SUSC_THRESHOLDS + [np.inf]
    cls = np.zeros(dom1.shape, "uint8")
    for i in range(5):
        cls[dom1 & (prob > edges[i]) & (prob <= edges[i + 1])] = i + 1
    px_ha = res * res / 1e4

    to_da = lambda a, name: tmpl.copy(data=a).rename(name)
    print("  ML cross-validated metrics:")
    for k, v in metrics.items():
        print(f"    {k}: {v}")
    return {
        "probability": to_da(prob, "bank_erosion_probability"),
        "agreement": to_da(agree, "model_agreement"),
        "susceptibility_class": to_da(cls, "bank_erosion_susceptibility"),
        "summary": {
            "target": "Bank loss (stable land or chars) between the baseline and current windows",
            "predictors_from": "baseline window conditions; predictions use current conditions",
            "training_pixels": int(n_s), "positive_share": round(float(y.mean()), 3),
            "domain_pixels": int(n_dom), "max_distance_m": max_d,
            "cv": f"spatial block cross-validation, {len(np.unique(groups))} blocks along the river",
            "metrics": metrics,
            "feature_importance": importance,
            "susceptibility_area_ha": {SUSC_LABELS[i]: round(float((cls == i).sum()) * px_ha, 1)
                                       for i in range(1, 6)},
            "thresholds": SUSC_THRESHOLDS,
        },
    }
