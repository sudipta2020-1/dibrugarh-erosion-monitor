"""Fortnightly district report (PDF) in English and Assamese.

Reads the data already published for the dashboard (docs/swift/data and
docs/data/swift) and writes, for the district in config.yaml:

  docs/reports/report_<date>_en.pdf, report_<date>_as.pdf   dated copies
  docs/reports/latest_en.pdf, latest_as.pdf                 latest copies
  docs/reports/index.json                                   list of reports and their key numbers

The report workflow runs after every weekly SWiFT-Bank run. A new report is made
only when the last one is at least 13 days old, so reports come every two weeks.
Use --force to make one now. The PDF is printed by headless Chrome, which shapes
Assamese script correctly.
"""
import argparse
import base64
import datetime as dt
import html
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import matplotlib
import pandas as pd
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

GAP_DAYS = 13
TOP_N = 12
PAGES = "https://sudipta2020-1.github.io"
SITE = "https://sites.google.com/dibru.ac.in/dibrugarhsoil"
URG_COLOR = {"Immediate": "#c62828", "Before monsoon": "#ef6c00", "Routine": "#2e7d32", "Monitor": "#1565c0"}

BANK, CHAR, ACC = "Bank erosion (stable land to water)", "Char or sandbar lost (within river belt)", "Accretion (water to land)"
VEG, BARE, AI = "Vegetation loss", "New bare soil", "Likely bank erosion (AI prediction)"
INLAND = "New inland water (pond or flooding)"

# ------------------------------------------------------------------ wording
T = {
    "en": {
        "lang": "en", "dept": "Soil Conservation Department, Government of Assam",
        "title": "Soil Erosion Monitoring Report", "district": "{d} District",
        "report_date": "Report date: {date}", "windows": "Satellite images compared: {base} and {cur}",
        "summary": "Summary", "keynums": "Key figures",
        "k_bank": "Bank erosion", "k_char": "Char or sandbar lost", "k_acc": "New land (accretion)",
        "k_veg": "Vegetation loss", "k_bare": "New bare soil", "k_imm": "Sites for immediate action",
        "k_pre": "Sites to check before the monsoon", "k_people": "People within 500 m of immediate-action sites",
        "ha": "ha", "map": "Priority sites", "map_note": "Numbers are the site ranks in the table. Blue lines: river bank today (SWiFT-Bank). Grey line: district boundary.",
        "change": "Change since the last report",
        "first": "This is the first report in the series.",
        "same": "No new clear satellite window since the report of {date}, so the figures are unchanged. The next dry-season window (November to February) will update them.",
        "diff": "Compared with the report of {date}: bank erosion {bank} ha, char loss {char} ha, sites for immediate action {imm}.",
        "table": "Priority sites for field verification (top {n} of {total})",
        "cols": ["Rank", "Site", "Type", "Urgency", "Lead agency", "Area (ha)", "Bank retreat (m/yr)", "People within 500 m", "Nearest place", "Location"],
        "open": "map",
        "todo": "What to do",
        "about": "About this report",
        "about_text": ("This report is made automatically every two weeks from Sentinel-2 satellite images. River bank erosion is mapped with "
                       "SWiFT-Bank, a tested sub-pixel method at 10 m; vegetation loss, bare soil and soil-loss risk use 20 m layers. "
                       "Areas of bank erosion and char loss are land lost to the river. People within 500 m are counted from WorldPop 2020 "
                       "and are an upper estimate. All sites must be confirmed in the field before works are planned."),
        "links": "Full maps, data downloads and the field form: {site}",
        "summary_text": ("Between the {base} and {cur} windows, {bank} ha of stable land along the river turned into water and {char} ha of chars "
                         "or sandbars were lost, while {acc} ha of new land formed. {imm} sites need immediate bank protection, and about "
                         "{people} people live within 500 m of them. {pre} more sites should be checked in the field before the monsoon. "
                         "Away from the river, {veg} ha lost vegetation cover and {bare} ha became bare soil."),
        "urg": {"Immediate": "Immediate", "Before monsoon": "Before monsoon", "Routine": "Routine", "Monitor": "Monitor"},
        "agency": {"Water Resources Dept": "Water Resources", "Soil Conservation Dept": "Soil Conservation",
                   "Revenue / Soil Conservation Dept": "Revenue / Soil Conservation"},
        "cls": {BANK: "Bank erosion", CHAR: "Char or sandbar lost", ACC: "New land (accretion)", VEG: "Vegetation loss",
                BARE: "New bare soil", AI: "Likely bank erosion (AI)", INLAND: "New inland water"},
        "act": {BANK: "Inform the Water Resources Department for emergency bank protection (geobags, porcupines) before the monsoon. Alert the circle office and the district disaster management authority.",
                AI: "Predicted by the AI model, not yet seen. Check in the field before the monsoon; if bank cutting is seen, treat it as active bank erosion.",
                CHAR: "Loss inside the river channel. No structural work; keep watch. If people live on the char, inform them and the circle office.",
                VEG: "Check the cause (harvest, tea pruning, clearing). If a slope or bank was cleared, restore cover with grass, cover crops or trees.",
                BARE: "Check for earth cutting, brick kilns or construction. Use mulching or cover crops, and control runoff with bunds or silt traps.",
                ACC: "New land from river deposits. Keep watch and avoid permanent settlement."},
        "page": "Page",
    },
    "as": {
        "lang": "as", "dept": "মৃত্তিকা সংৰক্ষণ বিভাগ, অসম চৰকাৰ",
        "title": "মাটি খহনীয়া নিৰীক্ষণ প্ৰতিবেদন", "district": "{d} জিলা",
        "report_date": "প্ৰতিবেদনৰ তাৰিখ: {date}", "windows": "তুলনা কৰা উপগ্ৰহ চিত্ৰৰ সময়: {base} আৰু {cur}",
        "summary": "সাৰাংশ", "keynums": "মূল তথ্য",
        "k_bank": "পাৰ খহনীয়া", "k_char": "চৰ বা বালিচৰ ক্ষয়", "k_acc": "নতুন মাটি (পলস জমা)",
        "k_veg": "উদ্ভিদ আৱৰণ হ্ৰাস", "k_bare": "নতুন উদং মাটি", "k_imm": "তৎক্ষণাৎ ব্যৱস্থা লোৱাৰ স্থান",
        "k_pre": "বাৰিষাৰ আগতে পৰীক্ষা কৰিবলগীয়া স্থান", "k_people": "তৎক্ষণাৎ ব্যৱস্থাৰ স্থানৰ 500 মিটাৰৰ ভিতৰত থকা লোক",
        "ha": "হেক্টৰ", "map": "অগ্ৰাধিকাৰ স্থানসমূহ", "map_note": "সংখ্যাবোৰ তালিকাত থকা স্থানৰ ক্ৰম। নীলা ৰেখা: বৰ্তমানৰ নদীৰ পাৰ (SWiFT-Bank)। ধোঁৱাবৰণীয়া ৰেখা: জিলাৰ সীমা।",
        "change": "যোৱা প্ৰতিবেদনৰ পিছত হোৱা পৰিৱৰ্তন",
        "first": "এইখন এই শৃংখলাৰ প্ৰথম প্ৰতিবেদন।",
        "same": "{date} তাৰিখৰ প্ৰতিবেদনৰ পিছত নতুন পৰিষ্কাৰ উপগ্ৰহ চিত্ৰ পোৱা নাই, সেয়েহে তথ্য সলনি হোৱা নাই। অহা শুকান বতৰৰ (নৱেম্বৰৰ পৰা ফেব্ৰুৱাৰী) চিত্ৰই তথ্য নতুনকৈ দিব।",
        "diff": "{date} তাৰিখৰ প্ৰতিবেদনৰ তুলনাত: পাৰ খহনীয়া {bank} হেক্টৰ, চৰ ক্ষয় {char} হেক্টৰ, তৎক্ষণাৎ ব্যৱস্থাৰ স্থান {imm}।",
        "table": "পথাৰত পৰীক্ষাৰ বাবে অগ্ৰাধিকাৰ স্থান ({total}টাৰ ভিতৰত প্ৰথম {n}টা)",
        "cols": ["ক্ৰম", "স্থান নং", "প্ৰকাৰ", "জৰুৰীতা", "মূল বিভাগ", "কালি (হেক্টৰ)", "পাৰ পিছুৱাই যোৱা (মিটাৰ/বছৰ)", "500 মিটাৰৰ ভিতৰত লোক", "ওচৰৰ ঠাই", "অৱস্থান"],
        "open": "মানচিত্ৰ",
        "todo": "কি কৰিব লাগে",
        "about": "এই প্ৰতিবেদনৰ বিষয়ে",
        "about_text": ("এই প্ৰতিবেদন প্ৰতি দুই সপ্তাহত Sentinel-2 উপগ্ৰহ চিত্ৰৰ পৰা স্বয়ংক্ৰিয়ভাৱে প্ৰস্তুত কৰা হয়। নদীৰ পাৰ খহনীয়া 10 মিটাৰ বিভেদনত "
                       "পৰীক্ষিত SWiFT-Bank পদ্ধতিৰে নিৰ্ণয় কৰা হৈছে; উদ্ভিদ আৱৰণ হ্ৰাস, উদং মাটি আৰু মাটি ক্ষয়ৰ আশংকা 20 মিটাৰ তথ্যৰে নিৰ্ণয় কৰা হৈছে। "
                       "পাৰ খহনীয়া আৰু চৰ ক্ষয়ৰ কালি হৈছে নদীয়ে গ্ৰাস কৰা মাটি। 500 মিটাৰৰ ভিতৰত থকা লোকৰ সংখ্যা WorldPop 2020ৰ পৰা লোৱা হৈছে আৰু ই সৰ্বোচ্চ অনুমান। "
                       "কাম আৰম্ভ কৰাৰ আগতে সকলো স্থান পথাৰত নিশ্চিত কৰিব লাগে।"),
        "links": "সম্পূৰ্ণ মানচিত্ৰ, তথ্য ডাউনলোড আৰু পথাৰ প্ৰপত্ৰ: {site}",
        "summary_text": ("{base} আৰু {cur} সময়ছোৱাৰ মাজত নদীৰ পাৰৰ {bank} হেক্টৰ স্থায়ী মাটি পানীলৈ পৰিণত হৈছে আৰু {char} হেক্টৰ চৰ বা বালিচৰ ক্ষয় হৈছে; "
                         "আনহাতে {acc} হেক্টৰ নতুন মাটি গঢ় লৈছে। {imm}টা স্থানত তৎক্ষণাৎ পাৰ সুৰক্ষাৰ প্ৰয়োজন, আৰু এই স্থানবোৰৰ 500 মিটাৰৰ ভিতৰত প্ৰায় "
                         "{people} জন লোক বাস কৰে। বাৰিষাৰ আগতে আৰু {pre}টা স্থান পথাৰত পৰীক্ষা কৰিব লাগে। নদীৰ পৰা আঁতৰত {veg} হেক্টৰ মাটিৰ উদ্ভিদ আৱৰণ হ্ৰাস পাইছে "
                         "আৰু {bare} হেক্টৰ মাটি উদং হৈছে।"),
        "urg": {"Immediate": "তৎক্ষণাৎ", "Before monsoon": "বাৰিষাৰ আগতে", "Routine": "নিয়মীয়া", "Monitor": "নজৰত ৰাখক"},
        "agency": {"Water Resources Dept": "জলসম্পদ বিভাগ", "Soil Conservation Dept": "মৃত্তিকা সংৰক্ষণ বিভাগ",
                   "Revenue / Soil Conservation Dept": "ৰাজহ / মৃত্তিকা সংৰক্ষণ বিভাগ"},
        "cls": {BANK: "পাৰ খহনীয়া", CHAR: "চৰ বা বালিচৰ ক্ষয়", ACC: "নতুন মাটি (পলস জমা)", VEG: "উদ্ভিদ আৱৰণ হ্ৰাস",
                BARE: "নতুন উদং মাটি", AI: "সম্ভাৱ্য পাৰ খহনীয়া (AI)", INLAND: "নতুন ভিতৰুৱা পানী"},
        "act": {BANK: "বাৰিষাৰ আগতে জৰুৰীকালীন পাৰ সুৰক্ষাৰ (জিঅ'-বেগ, পৰ্কুপাইন) বাবে জলসম্পদ বিভাগক জনাওক। চক্ৰ কাৰ্যালয় আৰু জিলা দুৰ্যোগ ব্যৱস্থাপনা প্ৰাধিকৰণক সতৰ্ক কৰক।",
                AI: "AI আৰ্হিৰ পূৰ্বানুমান, এতিয়াও দেখা পোৱা হোৱা নাই। বাৰিষাৰ আগতে পথাৰত পৰীক্ষা কৰক; পাৰ খহা দেখা গ'লে সক্ৰিয় পাৰ খহনীয়া হিচাপে গণ্য কৰক।",
                CHAR: "নদীৰ ভিতৰৰ ক্ষয়। নিৰ্মাণ কাৰ্যৰ প্ৰয়োজন নাই; নজৰ ৰাখক। চৰত লোক বাস কৰিলে তেওঁলোকক আৰু চক্ৰ কাৰ্যালয়ক জনাওক।",
                VEG: "কাৰণ পৰীক্ষা কৰক (শস্য চপোৱা, চাহ গছ ছঁটা, বন নিকা কৰা)। ঢাল বা পাৰৰ গছ-গছনি কটা হ'লে ঘাঁহ, আৱৰণ শস্য বা গছ ৰুই আৱৰণ ঘূৰাই আনক।",
                BARE: "মাটি কটা, ইটাভাটা বা নিৰ্মাণ কাৰ্য আছে নেকি পৰীক্ষা কৰক। মাল্চিং বা আৱৰণ শস্য ব্যৱহাৰ কৰক, আৰু বান্ধ বা পলস-ফান্দেৰে পানীৰ সোঁত নিয়ন্ত্ৰণ কৰক।",
                ACC: "নদীৰ পলসেৰে গঢ় লোৱা নতুন মাটি। নজৰ ৰাখক আৰু স্থায়ী বসতি নকৰিব।"},
        "page": "পৃষ্ঠা",
    },
}
DISTRICT_AS = {"Dibrugarh": "ডিব্ৰুগড়", "Majuli": "মাজুলী"}


def inr(v, d=0):
    """Indian digit grouping, e.g. 1,43,664."""
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    neg = v < 0
    s = f"{abs(v):.{d}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def signed(v, d=0):
    return ("+" if v > 0 else "") + inr(v, d)


def ddmmyyyy(s):
    return dt.date.fromisoformat(s[:10]).strftime("%d-%m-%Y")


def window(p):
    return f"{ddmmyyyy(p[0])} – {ddmmyyyy(p[1])}"


# ------------------------------------------------------------------ data
def gather(cfg):
    pub = Path(cfg["publish_dir"])
    dash = pub.parent / "swift" / "data"
    S = json.loads((dash / "summary.json").read_text())
    H = pd.read_csv(dash / "hotspots.csv")
    ch = S.get("change_area_ha", {})
    imm = H[H.urgency == "Immediate"]
    k = {
        "bank": ch.get(BANK, 0.0), "char": ch.get(CHAR, 0.0), "acc": ch.get(ACC, 0.0),
        "veg": ch.get(VEG, 0.0), "bare": ch.get(BARE, 0.0),
        "imm": int(S.get("sites_by_urgency", {}).get("Immediate", len(imm))),
        "pre": int(S.get("sites_by_urgency", {}).get("Before monsoon", 0)),
        "people": float(imm.population.fillna(0).sum()),
    }
    return {"S": S, "H": H, "k": k, "dash": dash, "pub": pub,
            "periods": {"baseline": S["period_baseline"], "current": S["period_current"]}}


def draw_map(cfg, D):
    """District outline, today's bank lines and the priority sites (no text but rank numbers)."""
    crs = cfg["crs"]
    aoi = gpd.read_file(D["dash"] / "aoi.geojson").to_crs(crs)
    fig, ax = plt.subplots(figsize=(9.2, 5.6), dpi=170)
    aoi.boundary.plot(ax=ax, color="#8a8a80", linewidth=0.9)
    aoi.plot(ax=ax, color="#f6f4ee", zorder=0)
    lines = D["pub"] / "swift" / "banklines_current.geojson"
    if lines.exists():
        gpd.read_file(lines).to_crs(crs).plot(ax=ax, color="#3b7fc4", linewidth=0.45, zorder=1)
    H = D["H"]
    pts = gpd.GeoDataFrame(H, geometry=gpd.points_from_xy(H.lon, H.lat), crs=4326).to_crs(crs)
    for urg, col in URG_COLOR.items():
        s = pts[pts.urgency == urg]
        if len(s):
            ax.scatter(s.geometry.x, s.geometry.y, s=26, color=col, edgecolor="white", linewidth=0.6, zorder=3)
    for _, r in pts[pts["rank"] <= TOP_N].iterrows():
        ax.annotate(str(int(r["rank"])), (r.geometry.x, r.geometry.y), xytext=(4, 4), textcoords="offset points",
                    fontsize=7.5, fontweight="bold", color="#1d1d1b", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
    x0, y0, x1, y1 = aoi.total_bounds
    pad = 0.03 * max(x1 - x0, y1 - y0)
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y0 - pad, y1 + pad)
    # 10 km scale bar and north arrow
    sx, sy = x0 + pad * 0.3, y0 - pad * 0.4
    ax.plot([sx, sx + 10000], [sy, sy], color="#1d1d1b", linewidth=2.2, solid_capstyle="butt")
    ax.text(sx + 5000, sy + pad * 0.25, "10 km", ha="center", va="bottom", fontsize=7.5)
    ax.annotate("N", xy=(x1 + pad * 0.5, y1), xytext=(x1 + pad * 0.5, y1 - 2.2 * pad), ha="center", fontsize=9,
                fontweight="bold", arrowprops=dict(arrowstyle="-|>", color="#1d1d1b", lw=1.2))
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ html
CSS = """
@page { size: A4; margin: 11mm 11mm 12mm 11mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #1d1d1b; font-size: 9.6pt; line-height: 1.38;
       font-family: "Noto Sans", "Source Sans 3", "Noto Sans Bengali", Arial, sans-serif; }
body.as { font-family: "Noto Sans Bengali", "Noto Sans", Arial, sans-serif; font-size: 9.4pt; line-height: 1.5; }
.head { background: linear-gradient(110deg, #0f5d73, #1f8a8a); color: #fff; border-radius: 6px; padding: 9px 14px 10px; }
.head .dept { font-size: 8pt; letter-spacing: .04em; text-transform: uppercase; opacity: .9; }
body.as .head .dept { text-transform: none; letter-spacing: 0; }
.head h1 { margin: 2px 0 1px; font-size: 16pt; font-weight: 700; }
.head .meta { display: flex; justify-content: space-between; gap: 10px; font-size: 8.4pt; opacity: .95; flex-wrap: wrap; }
h2 { font-size: 11pt; margin: 9px 0 4px; color: #0f5d73; border-bottom: 1.5px solid #d9e7ea; padding-bottom: 2px; }
p { margin: 0 0 5px; }
.kp { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; margin-top: 5px; }
.kp div { border: 1px solid #e2ded4; border-top: 3px solid var(--c); border-radius: 5px; padding: 4px 7px; }
.kp b { display: block; font-size: 13.5pt; line-height: 1.15; }
.kp span { font-size: 7.8pt; color: #5a5a54; display: block; line-height: 1.25; }
.map { text-align: center; margin-top: 3px; }
.map img { width: 100%; max-height: 112mm; object-fit: contain; }
.legend { display: flex; gap: 12px; justify-content: center; font-size: 8pt; color: #3a3a36; margin-top: 2px; flex-wrap: wrap; }
.legend i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
.note { font-size: 7.9pt; color: #5a5a54; }
.box { background: #f3f7f8; border-left: 3px solid #1f8a8a; padding: 5px 9px; border-radius: 4px; }
table { width: 100%; border-collapse: collapse; font-size: 8pt; }
th { background: #e9f1f3; text-align: left; font-weight: 600; padding: 3px 4px; vertical-align: bottom; }
td { padding: 3px 4px; border-bottom: 1px solid #ece9e1; vertical-align: top; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; padding: 0 5px; border-radius: 8px; color: #fff; font-size: 7.4pt; white-space: nowrap; }
.todo { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; }
.todo div { font-size: 8.2pt; }
.todo b { color: #0f5d73; }
a { color: #0f5d73; text-decoration: none; }
.pb { break-before: page; }
.foot { margin-top: 6px; font-size: 7.8pt; color: #5a5a54; }
"""


def render_html(cfg, D, lang, prev, map_uri, today):
    L = T[lang]
    S, H, k = D["S"], D["H"], D["k"]
    dname = S["district"]
    dshow = DISTRICT_AS.get(dname, dname) if lang == "as" else dname
    site = f"{SITE}/{dname.lower()}-district"
    base, cur = window(S["period_baseline"]), window(S["period_current"])
    e = html.escape

    if prev is None:
        change = L["first"]
    elif prev.get("periods") == D["periods"]:
        change = L["same"].format(date=ddmmyyyy(prev["date"]))
    else:
        pk = prev["k"]
        change = L["diff"].format(date=ddmmyyyy(prev["date"]), bank=signed(k["bank"] - pk["bank"], 1),
                                  char=signed(k["char"] - pk["char"], 1), imm=signed(k["imm"] - pk["imm"]))

    summary = L["summary_text"].format(base=base, cur=cur, bank=inr(k["bank"], 1), char=inr(k["char"], 1), acc=inr(k["acc"], 1),
                                       imm=k["imm"], people=inr(k["people"]), pre=k["pre"], veg=inr(k["veg"]), bare=inr(k["bare"]))
    kp = [(L["k_bank"], inr(k["bank"], 1) + " " + L["ha"], "#c62828"), (L["k_char"], inr(k["char"], 1) + " " + L["ha"], "#e57373"),
          (L["k_imm"], str(k["imm"]), "#c62828"), (L["k_people"], inr(k["people"]), "#7b1fa2"),
          (L["k_acc"], inr(k["acc"], 1) + " " + L["ha"], "#1565c0"), (L["k_pre"], str(k["pre"]), "#ef6c00"),
          (L["k_veg"], inr(k["veg"]) + " " + L["ha"], "#2e7d32"), (L["k_bare"], inr(k["bare"]) + " " + L["ha"], "#8d6e63")]
    kp_html = "".join(f'<div style="--c:{c}"><b>{e(v)}</b><span>{e(t)}</span></div>' for t, v, c in kp)
    legend = "".join(f'<span><i style="background:{c}"></i>{e(L["urg"][u])}</span>' for u, c in URG_COLOR.items())

    top = H.sort_values("rank").head(TOP_N)
    rows = []
    for _, r in top.iterrows():
        urg = r["urgency"]
        loc = f'{r["lat"]:.4f}<br>{r["lon"]:.4f}'
        gm = f'https://www.google.com/maps?q={r["lat"]:.6f},{r["lon"]:.6f}'
        ret = r.get("retreat_m_per_yr")
        rows.append(
            f'<tr><td class="n">{int(r["rank"])}</td><td style="white-space:nowrap">{e(str(r["site_id"]))}</td><td>{e(L["cls"].get(r["class"], r["class"]))}</td>'
            f'<td><span class="pill" style="background:{URG_COLOR.get(urg, "#666")}">{e(L["urg"].get(urg, urg))}</span></td>'
            f'<td>{e(L["agency"].get(r["lead_agency"], r["lead_agency"]))}</td><td class="n">{inr(r["area_ha"], 1)}</td>'
            f'<td class="n">{inr(ret, 0) if pd.notna(ret) else "–"}</td><td class="n">{inr(r["population"]) if pd.notna(r["population"]) else "–"}</td>'
            f'<td>{e(str(r["nearest_place"]))}</td><td style="white-space:nowrap"><a href="{gm}">{loc}</a></td></tr>')
    cols = L["cols"]
    num = {0, 5, 6, 7}
    thead = "".join(f'<th class="{"n" if i in num else ""}">{e(c)}</th>' for i, c in enumerate(cols))
    present = [c for c in [BANK, AI, CHAR, VEG, BARE, ACC] if c in set(H["class"])]
    todo = "".join(f'<div><b>{e(L["cls"][c])}.</b> {e(L["act"][c])}</div>' for c in present)

    return f"""<!doctype html><html lang="{L['lang']}"><head><meta charset="utf-8">
<title>{e(L['title'])} · {e(dname)}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;600;700&family=Noto+Sans+Bengali:wght@400;600;700&display=block">
<style>{CSS}</style></head>
<body class="{lang}">
<div class="head"><div class="dept">{e(L['dept'])}</div>
<h1>{e(L['title'])} · {e(L['district'].format(d=dshow))}</h1>
<div class="meta"><span>{e(L['report_date'].format(date=today.strftime('%d-%m-%Y')))}</span><span>{e(L['windows'].format(base=base, cur=cur))}</span></div></div>

<h2>{e(L['summary'])}</h2>
<p>{e(summary)}</p>
<div class="kp">{kp_html}</div>

<h2>{e(L['map'])}</h2>
<div class="map"><img src="{map_uri}" alt=""></div>
<div class="legend">{legend}</div>
<p class="note" style="text-align:center">{e(L['map_note'])}</p>

<h2>{e(L['change'])}</h2>
<p class="box">{e(change)}</p>

<h2 class="pb">{e(L['table'].format(n=len(top), total=len(H)))}</h2>
<table><thead><tr>{thead}</tr></thead><tbody>{''.join(rows)}</tbody></table>

<h2>{e(L['todo'])}</h2>
<div class="todo">{todo}</div>

<h2>{e(L['about'])}</h2>
<p>{e(L['about_text'])}</p>
<p class="foot">{e(L['links'].format(site=''))}<a href="{site}">{e(site)}</a></p>
</body></html>"""


# ------------------------------------------------------------------ pdf
def find_chrome():
    for c in [os.environ.get("CHROME"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]:
        if c and shutil.which(c):
            return shutil.which(c)
    raise SystemExit("Chrome or Chromium is needed to print the PDF")


def to_pdf(html_text, out):
    chrome = find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "report.html"
        src.write_text(html_text, encoding="utf-8")
        cmd = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
               "--print-to-pdf-no-header", f"--print-to-pdf={out}", "--virtual-time-budget=15000",
               f"--user-data-dir={tmp}/profile", src.as_uri()]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    if not Path(out).exists() or Path(out).stat().st_size < 5000:
        raise SystemExit(f"PDF was not written: {out}")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--force", action="store_true", help="make a report even if the last one is recent")
    ap.add_argument("--date", help="report date (YYYY-MM-DD), default today")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(dt.timezone.utc).date()

    out = Path(cfg["publish_dir"]).parent / "reports"
    out.mkdir(parents=True, exist_ok=True)
    idx_path = out / "index.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else {"reports": []}
    earlier = [r for r in idx["reports"] if r["date"] < today.isoformat()]
    last = max(idx["reports"], key=lambda r: r["date"]) if idx["reports"] else None
    if last and not args.force and (today - dt.date.fromisoformat(last["date"])).days < GAP_DAYS:
        print(f"Last report {last['date']} is less than {GAP_DAYS} days old; no new report.")
        return 0
    prev = max(earlier, key=lambda r: r["date"]) if earlier else None

    D = gather(cfg)
    map_uri = draw_map(cfg, D)
    tag = today.isoformat()
    files = {}
    for lang in ("en", "as"):
        name = f"report_{tag}_{lang}.pdf"
        to_pdf(render_html(cfg, D, lang, prev, map_uri, today), out / name)
        shutil.copyfile(out / name, out / f"latest_{lang}.pdf")
        files[lang] = name
        print(f"  wrote {name} ({(out / name).stat().st_size / 1024:.0f} KB)")

    entry = {"date": tag, "district": D["S"]["district"], "files": files, "periods": D["periods"],
             "k": {key: round(v, 1) if isinstance(v, float) else v for key, v in D["k"].items()}}
    idx["reports"] = [r for r in idx["reports"] if r["date"] != tag] + [entry]
    idx["reports"].sort(key=lambda r: r["date"], reverse=True)
    idx["latest"] = tag
    idx_path.write_text(json.dumps(idx, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
