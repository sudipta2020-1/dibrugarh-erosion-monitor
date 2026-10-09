"""
Accuracy assessment for the water-change map.

Design (Olofsson et al., 2014; Stehman, 2014)
--------------------------------------------
1. Stratified random sample. The map is split into strata (bank erosion of
   stable land, char loss, accretion, stable land, stable water) and a fixed
   number of points is drawn at random from each stratum. Rare change classes
   therefore get enough points, which simple random sampling would not give.
2. Reference labels. Each point is labelled by a person who looks at the
   baseline and current Sentinel-2 image chips and at high-resolution imagery,
   without seeing the map class (blind interpretation). The label records what
   actually happened on the ground, not whether the map was right, so the same
   labels can test every later version of the method for the same periods.
3. Estimation. Accuracy and class areas are estimated with the stratified
   estimators of Stehman (2014), which remain valid when the map being tested
   differs from the map used to draw the strata. Areas are reported with 95%
   confidence intervals.

Files (all under docs/data/validation/, served with the dashboard):
  sample.csv        id, lat, lon               (what the interpreter sees)
  sample_meta.json  periods, strata, stratum sizes, random seed
  strata.csv        id, stratum                (kept apart from sample.csv)
  chips/<id>_b.jpg, chips/<id>_c.jpg  baseline and current image chips
Reference labels are read from data/validation_labels.csv (id, reference, ...).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

# Classes used for both strata and reference labels
CLASSES = {
    1: "Bank erosion of stable land",
    5: "Char or sandbar lost",
    2: "Accretion",
    10: "Stable land",
    11: "Stable water",
}
REF_CODES = {"erosion_mainland": 1, "erosion_char": 5, "accretion": 2,
             "stable_land": 10, "stable_water": 11}
DEFAULT_N = {1: 50, 5: 50, 2: 50, 10: 75, 11: 75}


def map_classes(change, base_water, curr_water, inside):
    """Collapse the change map into the five assessment classes (0 = outside)."""
    c = np.asarray(change)
    wb, wc = np.asarray(base_water, bool), np.asarray(curr_water, bool)
    out = np.zeros(c.shape, dtype="uint8")
    out[~wb & ~wc] = 10
    out[wb & wc] = 11
    out[c == 1] = 1
    out[c == 5] = 5
    out[c == 2] = 2
    out[~np.asarray(inside, bool)] = 0
    return out


def draw_sample(strata, transform, crs, n_per=None, seed=42):
    """Stratified random sample of pixel centres. Returns (DataFrame, stratum sizes)."""
    from pyproj import Transformer
    n_per = {int(k): int(v) for k, v in (n_per or DEFAULT_N).items()}
    rng = np.random.default_rng(seed)
    rows, sizes = [], {}
    for h in CLASSES:
        rr, cc = np.nonzero(strata == h)
        sizes[h] = int(rr.size)
        if rr.size == 0:
            continue
        k = min(n_per.get(h, 50), rr.size)
        pick = rng.choice(rr.size, size=k, replace=False)
        for i in pick:
            rows.append((h, int(rr[i]), int(cc[i])))
    rng.shuffle(rows)                      # mix strata so the interpreter cannot guess
    to_ll = Transformer.from_crs(crs, 4326, always_xy=True)
    recs = []
    for n, (h, r, c) in enumerate(rows, start=1):
        x, y = transform * (c + 0.5, r + 0.5)
        lon, lat = to_ll.transform(x, y)
        recs.append({"id": f"P{n:03d}", "stratum": h, "row": r, "col": c,
                     "lat": round(lat, 6), "lon": round(lon, 6)})
    return pd.DataFrame(recs), sizes


def _rgb(s2, stretch):
    a = np.stack([np.asarray(s2[b]) for b in ["B04", "B03", "B02"]], axis=-1)
    lo, hi = stretch
    return np.clip((a - lo) / (hi - lo), 0, 1)


def write_chips(df, s2_base, s2_curr, out_dir, half=32, scale=4):
    """Image chips (2*half pixels wide) around each point, baseline and current,
    with a square marking the sample pixel. Both periods share one stretch."""
    from PIL import Image, ImageDraw
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    allv = np.concatenate([np.asarray(s2_curr[b]).ravel() for b in ["B04", "B03", "B02"]])
    allv = allv[np.isfinite(allv)]
    stretch = tuple(np.percentile(allv, [2, 98])) if allv.size else (0, 0.3)
    imgs = {"b": _rgb(s2_base, stretch), "c": _rgb(s2_curr, stretch)}
    H, W = imgs["b"].shape[:2]
    for _, p in df.iterrows():
        r0, c0 = p["row"] - half, p["col"] - half
        for tag, img in imgs.items():
            tile = np.zeros((2 * half, 2 * half, 3))
            rs, cs = max(r0, 0), max(c0, 0)
            re, ce = min(r0 + 2 * half, H), min(c0 + 2 * half, W)
            tile[rs - r0:re - r0, cs - c0:ce - c0] = img[rs:re, cs:ce]
            tile = np.nan_to_num(tile)
            im = Image.fromarray((tile * 255).astype("uint8")).resize(
                (2 * half * scale, 2 * half * scale), Image.NEAREST)
            d = ImageDraw.Draw(im)
            m = half * scale
            d.rectangle([m - 2, m - 2, m + scale + 1, m + scale + 1], outline=(255, 230, 0), width=2)
            im.save(out_dir / f"{p['id']}_{tag}.jpg", quality=82)


def create(vdir, strata, template, s2_base, s2_curr, cfg_v, periods, res):
    """Draw the sample once and write all validation files."""
    vdir = Path(vdir)
    df, sizes = draw_sample(strata, template.rio.transform(), template.rio.crs,
                            cfg_v.get("sample_per_stratum"), cfg_v.get("seed", 42))
    vdir.mkdir(parents=True, exist_ok=True)
    df[["id", "lat", "lon"]].to_csv(vdir / "sample.csv", index=False)
    df[["id", "stratum"]].to_csv(vdir / "strata.csv", index=False)
    write_chips(df, s2_base, s2_curr, vdir / "chips")
    meta = {"period_baseline": periods[0], "period_current": periods[1],
            "resolution_m": res, "seed": cfg_v.get("seed", 42),
            "classes": {str(k): v for k, v in CLASSES.items()},
            "stratum_pixels": {str(k): v for k, v in sizes.items()},
            "points": int(len(df)),
            "points_per_stratum": {str(k): int((df["stratum"] == k).sum()) for k in CLASSES}}
    (vdir / "sample_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"  validation sample: {len(df)} points written to {vdir}")
    return meta


def _stratified(y_by_h, W, n_h):
    """Stratified mean and its standard error. y_by_h: dict h -> array of y."""
    est, var = 0.0, 0.0
    for h, y in y_by_h.items():
        if len(y) == 0:
            continue
        est += W[h] * np.mean(y)
        if len(y) > 1:
            var += W[h] ** 2 * np.var(y, ddof=1) / len(y)
    return est, np.sqrt(var)


def _ratio(y_by_h, x_by_h, W):
    """Stratified ratio estimator R = Y/X with its standard error (Stehman, 2014, eq. 27-28)."""
    Y = sum(W[h] * np.mean(y) for h, y in y_by_h.items() if len(y))
    X = sum(W[h] * np.mean(x_by_h[h]) for h, y in y_by_h.items() if len(y))
    if X == 0:
        return np.nan, np.nan
    R = Y / X
    v = 0.0
    for h in y_by_h:
        y, x = np.asarray(y_by_h[h], float), np.asarray(x_by_h[h], float)
        n = len(y)
        if n < 2:
            continue
        sy, sx = np.var(y, ddof=1), np.var(x, ddof=1)
        sxy = np.cov(y, x, ddof=1)[0, 1]
        v += W[h] ** 2 * (sy + R ** 2 * sx - 2 * R * sxy) / n
    return R, np.sqrt(v) / X


def assess(vdir, labels_path, map_cls, px_ha):
    """Accuracy and area estimates for the current map. Returns a dict or None."""
    vdir, labels_path = Path(vdir), Path(labels_path)
    if not (vdir / "sample_meta.json").exists() or not labels_path.exists():
        return None
    meta = json.loads((vdir / "sample_meta.json").read_text())
    pts = pd.read_csv(vdir / "sample.csv").merge(pd.read_csv(vdir / "strata.csv"), on="id")
    lab = pd.read_csv(labels_path)
    lab["reference"] = lab["reference"].astype(str).str.strip().str.lower()
    df = pts.merge(lab[["id", "reference"]], on="id", how="inner")
    df = df[df["reference"].isin(REF_CODES)]
    if len(df) < 20:
        return {"status": "too_few_labels", "labelled": int(len(df))}
    df["ref"] = df["reference"].map(REF_CODES)

    # Map class of each point on the CURRENT map (may differ from the stratum)
    from pyproj import Transformer
    t = Transformer.from_crs(4326, map_cls.rio.crs, always_xy=True)
    inv = ~map_cls.rio.transform()
    arr = np.asarray(map_cls)
    mc = []
    for lon, lat in zip(df["lon"], df["lat"]):
        x, y = t.transform(lon, lat)
        c, r = inv * (x, y)
        r, c = int(np.floor(r)), int(np.floor(c))
        mc.append(int(arr[r, c]) if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1] else 0)
    df["map"] = mc

    Nh = {int(k): v for k, v in meta["stratum_pixels"].items()}
    N = sum(Nh.values())
    W = {h: Nh[h] / N for h in Nh}
    groups = {h: g for h, g in df.groupby("stratum")}
    strata = [h for h in CLASSES if h in groups]

    def by_h(fn):
        return {h: fn(groups[h]).astype(float).values for h in strata}

    oa, oa_se = _stratified(by_h(lambda g: g["map"] == g["ref"]), W, None)
    per_class, matrix = {}, {}
    for k, name in CLASSES.items():
        ua, ua_se = _ratio(by_h(lambda g: (g["map"] == k) & (g["ref"] == k)),
                           by_h(lambda g: g["map"] == k), W)
        pa, pa_se = _ratio(by_h(lambda g: (g["map"] == k) & (g["ref"] == k)),
                           by_h(lambda g: g["ref"] == k), W)
        p_ref, p_ref_se = _stratified(by_h(lambda g: g["ref"] == k), W, None)
        mapped_ha = float((arr == k).sum()) * px_ha
        per_class[name] = {
            "users_accuracy": None if np.isnan(ua) else round(ua, 3),
            "users_ci95": None if np.isnan(ua_se) else round(1.96 * ua_se, 3),
            "producers_accuracy": None if np.isnan(pa) else round(pa, 3),
            "producers_ci95": None if np.isnan(pa_se) else round(1.96 * pa_se, 3),
            "mapped_area_ha": round(mapped_ha, 1),
            "estimated_area_ha": round(p_ref * N * px_ha, 1),
            "estimated_area_ci95_ha": round(1.96 * p_ref_se * N * px_ha, 1),
        }
        matrix[name] = {CLASSES[j]: round(float(sum(
            W[h] * np.mean((groups[h]["map"] == k) & (groups[h]["ref"] == j)) for h in strata)), 5)
            for j in CLASSES}
    res = {"status": "ok", "labelled": int(len(df)), "sample_points": meta["points"],
           "period_baseline": meta["period_baseline"], "period_current": meta["period_current"],
           "overall_accuracy": round(oa, 3), "overall_ci95": round(1.96 * oa_se, 3),
           "per_class": per_class, "area_proportion_matrix_map_rows": matrix,
           "method": "Stratified random sample; Stehman (2014) estimators; 95% confidence intervals"}
    return res
