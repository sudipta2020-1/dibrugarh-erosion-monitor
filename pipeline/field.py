"""
Mobile field verification workflow (KoboToolbox or ODK Collect).

Every monitoring run writes, in docs/data/field/:
  erosion_field_survey.xlsx   the survey form in XLSForm format (works offline on
                              Android with KoboCollect or ODK Collect, and in any
                              phone browser through the KoboToolbox web form)
  priority_sites.csv          the current priority sites, attached to the form as
                              a media file so officers pick a site from a list and
                              see its mapped location and suggested action

The form records the officer, GPS position, erosion type and severity, what
actually happened since the baseline year (the same classes as the accuracy
assessment), assets and households at risk, existing and recommended measures,
urgency, whether the satellite map matched, and up to three photographs.

Records come back as a CSV export from KoboToolbox, saved in the repository as
data/field_records.csv. Each run matches the records to the priority sites
(by GPS within `match_m` metres, else by site ID), marks each site as confirmed
or not, and reports how many visited sites were confirmed. This is the measure
of "usefulness of the priority ranking" asked for in the challenge.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

FORM_ID = "dibrugarh_erosion_field"
SITES_FILE = "priority_sites.csv"


def site_id(run_end, rank):
    """Short, readable ID: year-month of the run window end and the rank, e.g. 2602-07."""
    return f"{run_end[2:4]}{run_end[5:7]}-{int(rank):02d}"


def _short(cls):
    return str(cls).split(" (")[0]


def write_sites(hot, run_end, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in hot.sort_values("rank").to_dict("records"):
        place = r.get("nearest_place") or ""
        place = f" near {place}" if isinstance(place, str) and place else ""
        rows.append({"name": site_id(run_end, r["rank"]),
                     "label": f"#{int(r['rank'])} {_short(r['class'])}{place} ({r['lat']:.4f}, {r['lon']:.4f})",
                     "lat": r["lat"], "lon": r["lon"], "rank": int(r["rank"]),
                     "site_class": _short(r["class"]), "urgency": r.get("urgency", ""),
                     "action": r.get("recommended_action", "")})
    rows.append({"name": "NEW", "label": "New site (not on the list)", "lat": "", "lon": "",
                 "rank": "", "site_class": "", "urgency": "", "action": ""})
    pd.DataFrame(rows).to_csv(out_dir / SITES_FILE, index=False)
    return len(rows) - 1


def _survey():
    S = []

    def q(type_, name, label="", **kw):
        S.append({"type": type_, "name": name, "label": label, **kw})

    q("start", "start")
    q("end", "end")
    q("today", "today")
    q("deviceid", "deviceid")
    q("text", "officer", "Officer name", required="yes")
    q("text", "office", "Office / division")
    q("select_one_from_file " + SITES_FILE, "site_id", "Priority site", required="yes",
      appearance="minimal", hint="Choose the site from this month's list, or 'New site'.")
    q("calculate", "exp_lat", calculation="pulldata('priority_sites', 'lat', 'name', ${site_id})")
    q("calculate", "exp_lon", calculation="pulldata('priority_sites', 'lon', 'name', ${site_id})")
    q("calculate", "exp_class", calculation="pulldata('priority_sites', 'site_class', 'name', ${site_id})")
    q("calculate", "exp_action", calculation="pulldata('priority_sites', 'action', 'name', ${site_id})")
    q("note", "site_info", "Mapped as: **${exp_class}** at ${exp_lat}, ${exp_lon}. "
      "Suggested action: ${exp_action}", relevance="${site_id} != 'NEW'")
    q("geopoint", "gps", "Record your GPS position at the site", required="yes",
      hint="Stand at the eroding edge or the centre of the affected area. Wait for accuracy below 10 m.")
    q("select_one yesno3", "erosion_present", "Is there active soil erosion here?", required="yes")
    q("select_one etype", "erosion_type", "Type of erosion", required="yes",
      relevance="${erosion_present} = 'yes'")
    q("select_one sev", "severity", "Severity", required="yes", relevance="${erosion_present} = 'yes'")
    q("select_one act", "activity", "Is it active?", relevance="${erosion_present} = 'yes'")
    q("decimal", "retreat_m", "Estimated bank retreat in the last 12 months (m)",
      relevance="${erosion_type} = 'riverbank'", constraint=". >= 0 and . < 2000")
    q("decimal", "bank_height_m", "Bank height above water (m)",
      relevance="${erosion_type} = 'riverbank'", constraint=". >= 0 and . < 50")
    q("decimal", "gully_depth_m", "Gully depth (m)", relevance="${erosion_type} = 'gully'",
      constraint=". >= 0 and . < 100")
    q("decimal", "gully_width_m", "Gully width (m)", relevance="${erosion_type} = 'gully'",
      constraint=". >= 0 and . < 500")
    q("select_one ref", "reference_class", "Compared with 2020, what happened at this point?",
      required="yes", hint="Use the same meaning as on the validation page.")
    q("select_one yesno3", "map_agrees", "Does the satellite map match what you see?", required="yes")
    q("select_one lu", "land_use", "Main land use around the site")
    q("select_multiple assets", "assets_at_risk", "Assets at risk within about 500 m")
    q("integer", "households_at_risk", "Households at risk (approximate)", constraint=". >= 0 and . < 100000")
    q("select_multiple meas", "existing_measures", "Existing protection or conservation measures")
    q("select_one cond", "measures_condition", "Condition of existing measures",
      relevance="not(selected(${existing_measures}, 'none'))")
    q("select_multiple meas", "recommended_measures", "Recommended measures")
    q("select_one urg", "urgency", "Urgency", required="yes")
    q("image", "photo_1", "Photo 1: the eroding bank, gully or affected area", required="yes",
      parameters="max-pixels=1600")
    q("image", "photo_2", "Photo 2 (optional): wider view or assets at risk", parameters="max-pixels=1600")
    q("image", "photo_3", "Photo 3 (optional): existing measures", parameters="max-pixels=1600")
    q("text", "notes", "Notes", appearance="multiline")
    return S


CHOICES = {
    "yesno3": [("yes", "Yes"), ("no", "No"), ("unsure", "Unsure")],
    "etype": [("riverbank", "Riverbank erosion"), ("char", "Char or sandbar erosion"),
              ("sheet_rill", "Sheet or rill erosion"), ("gully", "Gully erosion"),
              ("landslide", "Landslide or slope failure"), ("other", "Other")],
    "sev": [("low", "Low"), ("moderate", "Moderate"), ("high", "High"), ("very_high", "Very high")],
    "act": [("active", "Active now"), ("recent", "Recent (last 1-2 years)"), ("old", "Old, stabilised")],
    "ref": [("erosion_mainland", "Bank erosion of stable land"), ("erosion_char", "Char or sandbar lost"),
            ("accretion", "Accretion (water became land)"), ("new_inland_water", "New inland water"),
            ("stable_land", "Stable land"), ("stable_water", "Stable water"), ("unsure", "Unsure")],
    "lu": [("cropland", "Cropland / paddy"), ("tea", "Tea garden"), ("settlement", "Settlement"),
           ("forest", "Forest / tree cover"), ("grass", "Grassland"), ("bare", "Bare land / sand"),
           ("char", "Char land"), ("other", "Other")],
    "assets": [("houses", "Houses"), ("road", "Road"), ("embankment", "Embankment"),
               ("school", "School"), ("health", "Health facility"), ("worship", "Place of worship"),
               ("cropland", "Cropland"), ("tea", "Tea garden"), ("power", "Power line / tower"),
               ("other", "Other"), ("none", "None")],
    "meas": [("none", "None"), ("geobags", "Geobags"), ("porcupines", "Porcupines (RCC or bamboo)"),
             ("revetment", "Revetment / bank lining"), ("spurs", "Spurs / groynes"),
             ("bank_vegetation", "Bank vegetation (bamboo, vetiver, grass)"),
             ("check_dams", "Check dams / gully plugs"), ("contour_bunds", "Contour or field bunds"),
             ("trenches", "Contour trenches"), ("cover_crops", "Cover crops / mulching"),
             ("drainage", "Drainage works"), ("relocation", "Relocation of households"),
             ("other", "Other")],
    "cond": [("good", "Good"), ("damaged", "Damaged"), ("failed", "Failed / washed away")],
    "urg": [("immediate", "Immediate"), ("before_monsoon", "Before monsoon"),
            ("routine", "Routine"), ("monitor", "Monitor only")],
}


def write_form(out_dir, version):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = ["type", "name", "label", "hint", "required", "relevance", "constraint",
            "calculation", "appearance", "parameters"]
    survey = pd.DataFrame(_survey()).reindex(columns=cols).fillna("")
    choices = pd.DataFrame([{"list_name": k, "name": n, "label": l}
                            for k, v in CHOICES.items() for n, l in v])
    settings = pd.DataFrame([{"form_title": "Dibrugarh soil erosion field verification",
                              "form_id": FORM_ID, "version": version, "allow_choice_duplicates": "yes"}])
    path = out_dir / "erosion_field_survey.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        survey.to_excel(xw, sheet_name="survey", index=False)
        choices.to_excel(xw, sheet_name="choices", index=False)
        settings.to_excel(xw, sheet_name="settings", index=False)
    return path


# ------------------------------------------------------------------ ingestion
def _col(df, name):
    """Find a column by its XLSForm name, allowing group prefixes ('grp/name')."""
    for c in df.columns:
        if c == name or c.endswith("/" + name):
            return c
    return None


def read_records(path):
    p = Path(path)
    if not p.exists():
        return None
    df = pd.read_csv(p, sep=None, engine="python", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    lat_c, lon_c = _col(df, "_gps_latitude"), _col(df, "_gps_longitude")
    if lat_c and lon_c:
        lat, lon = pd.to_numeric(df[lat_c], errors="coerce"), pd.to_numeric(df[lon_c], errors="coerce")
    else:
        g = df[_col(df, "gps")].fillna("").str.split(expand=True) if _col(df, "gps") else None
        lat = pd.to_numeric(g[0], errors="coerce") if g is not None else np.nan
        lon = pd.to_numeric(g[1], errors="coerce") if g is not None else np.nan
    out = pd.DataFrame({"lat": lat, "lon": lon})
    for k in ["site_id", "today", "officer", "erosion_present", "erosion_type", "severity",
              "reference_class", "map_agrees", "urgency", "households_at_risk", "retreat_m", "photo_1"]:
        c = _col(df, k)
        out[k] = df[c] if c else ""
    return out


# Field answers that confirm each kind of site. Site types not listed here are
# confirmed by the answer to "Is there active soil erosion here?".
EXPECTED = {"Bank erosion": {"erosion_mainland"},
            "Char or sandbar lost": {"erosion_char", "erosion_mainland"},
            "Accretion": {"accretion"},
            "New inland water": {"new_inland_water"}}


def _verdict(site_class, rec):
    ref = str(rec.get("reference_class", "") or "").lower()
    ep = str(rec.get("erosion_present", "") or "").lower()
    exp = EXPECTED.get(site_class)
    if exp and ref and ref not in ("unsure", "nan"):
        return "Confirmed" if ref in exp else "Not confirmed"
    return {"yes": "Confirmed", "no": "Not confirmed"}.get(ep, "Unsure")


def ingest(path, hot, run_end, crs, match_m=300.0):
    """Match field records to the current sites. Returns (hot with field columns,
    summary dict, GeoDataFrame of all records) or (hot, None, None) without records."""
    import geopandas as gpd
    rec = read_records(path)
    if rec is None or rec.empty:
        return hot, None, None
    g = gpd.GeoDataFrame(rec, geometry=gpd.points_from_xy(rec["lon"], rec["lat"]), crs="EPSG:4326")
    g = g[g.geometry.notna() & np.isfinite(rec["lat"]) & np.isfinite(rec["lon"])]
    hot = hot.copy()
    hot["site_id"] = [site_id(run_end, r) for r in hot["rank"]]
    status, dates, visits = [], [], []
    if len(g):
        gu = g.to_crs(crs)
        for _, s in hot.iterrows():
            # Records that name this site win; otherwise the nearest record within
            # match_m that was not filed for another site on the current list.
            m = gu[gu["site_id"] == s["site_id"]]
            if len(m) == 0:
                d = gu.distance(s.geometry)
                free = ~gu["site_id"].isin(set(hot["site_id"]) - {s["site_id"]})
                m = gu.assign(_d=d)[(d <= match_m) & free]
                m = m.sort_values("_d").head(1)
            if len(m) == 0:
                status.append(""), dates.append(""), visits.append(0)
                continue
            m = m.sort_values("today")
            last = m.iloc[-1]
            status.append(_verdict(_short(s["class"]), last))
            dates.append(str(last["today"])[:10])
            visits.append(len(m))
    else:
        status, dates, visits = [""] * len(hot), [""] * len(hot), [0] * len(hot)
    hot["field_status"], hot["field_date"], hot["field_visits"] = status, dates, visits

    visited = hot[hot["field_visits"] > 0]
    conf = int((visited["field_status"] == "Confirmed").sum())
    decided = int(visited["field_status"].isin(["Confirmed", "Not confirmed"]).sum())
    summary = {
        "records": int(len(rec)),
        "records_with_gps": int(len(g)),
        "sites_visited": int(len(visited)),
        "sites_confirmed": conf,
        "sites_not_confirmed": int((visited["field_status"] == "Not confirmed").sum()),
        "confirmation_rate": round(conf / decided, 3) if decided else None,
        "map_agreement_rate": (round(float((rec["map_agrees"].str.lower() == "yes").mean()), 3)
                               if len(rec) else None),
        "new_sites_reported": int((rec["site_id"].fillna("") == "NEW").sum()),
        "by_type": {t: {"visited": int((visited["class"] == t).sum()),
                        "confirmed": int(((visited["class"] == t) & (visited["field_status"] == "Confirmed")).sum())}
                    for t in sorted(visited["class"].unique())},
        "match_m": match_m,
    }
    return hot, summary, g
