"""
Recommended conservation measures for each priority site.

Each site gets three fields:
  urgency            Immediate / Before monsoon / Routine / Monitor
  lead_agency        the department usually responsible for the measure
  recommended_action short text for field teams

The rules use only what the system knows about a site: its type, the evidence
behind it (confidence), how fast the bank is retreating and what is at stake
within the buffer (people, roads, embankments, facilities). They are a first
screening, written so that the department can edit them in config.yaml
(section "interventions"); the field visit decides the final measure.

The measures listed are those commonly used in the Brahmaputra valley and in
watershed programmes: geobags and porcupines (RCC or bamboo) as temporary bank
protection, revetments and spurs as permanent works, bank vegetation (bamboo,
vetiver, native grasses), and contour bunds, trenches, check dams and cover
crops on slopes.
"""
import numpy as np

URGENCY_ORDER = {"Immediate": 0, "Before monsoon": 1, "Routine": 2, "Monitor": 3}

DEFAULTS = {
    "people_high": 500,          # people within the buffer that make a site urgent
    "retreat_fast_m_per_yr": 50,
    "exposure_high": 0.4,
    "slope_steep_deg": 8,
}

TEXT = {
    "bank_urgent": "Inform the Water Resources Department for emergency bank protection "
                   "(geobags, RCC or bamboo porcupines) before the monsoon, followed by a "
                   "permanent revetment or spurs. Alert the circle office and the district "
                   "disaster management authority; prepare relocation if retreat continues.",
    "bank_assets": "Plan bank protection with the Water Resources Department (porcupines or "
                   "geobags, then revetment). Plant bamboo and vetiver along the bank behind "
                   "the eroding edge.",
    "bank_low": "Stabilise the bank with vegetation (bamboo, vetiver, native grasses) and keep "
                "a setback for new construction. Monitor the bank line each month.",
    "char": "Char or sandbar loss inside the river channel. No structural work; monitor. "
            "If the char is inhabited, inform residents and the circle office.",
    "ai": "Predicted by the AI model, not yet observed. Field check before the monsoon; "
          "if bank cutting is seen, treat as active bank erosion.",
    "accretion": "New land from river deposition. Monitor; avoid permanent settlement. "
                 "Grass or tree planting can stabilise older deposits.",
    "inland_water": "New water away from the river: check for waterlogging, drainage "
                    "congestion or new ponds. Clear or restore drainage where needed.",
    "veg_loss": "Field check the cause (harvest, tea pruning, clearing, earth cutting). "
                "If land was cleared on a slope or bank, restore cover with grasses, "
                "cover crops or agroforestry.",
    "bare_soil": "Check for earth cutting, brick kilns or construction. Apply mulching or "
                 "cover crops; control runoff with bunds or silt traps.",
    "risk_steep": "Hillslope erosion risk. Contour trenches and staggered trenches, "
                  "vegetative barriers, and check dams or gully plugs in drainage lines.",
    "risk_gentle": "Sheet erosion risk. Contour bunds or field bunds, cover crops and "
                   "mulching; in tea gardens maintain drains and shade trees.",
}


def _num(v, default=0.0):
    try:
        v = float(v)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def recommend(row, p):
    """Return (urgency, lead_agency, action) for one site (a dict-like row)."""
    cls = str(row.get("class", ""))
    people = _num(row.get("population"))
    expo = _num(row.get("exposure_index"))
    retreat = _num(row.get("retreat_m_per_yr"))
    assets = (_num(row.get("road_km")) > 0.2 or _num(row.get("embankment_km")) > 0.1
              or _num(row.get("facilities")) > 0)
    conf = str(row.get("confidence", ""))

    if cls.startswith("Bank erosion"):
        urgent = (people >= p["people_high"] or _num(row.get("embankment_km")) > 0.1
                  or retreat >= p["retreat_fast_m_per_yr"])
        if urgent and conf in ("High", "Medium"):
            return "Immediate", "Water Resources Dept", TEXT["bank_urgent"]
        if urgent or assets or expo >= p["exposure_high"]:
            return "Before monsoon", "Water Resources Dept", TEXT["bank_assets"]
        return "Routine", "Soil Conservation Dept", TEXT["bank_low"]
    if cls.startswith("Char"):
        return "Monitor", "Water Resources Dept", TEXT["char"]
    if cls.startswith("Likely bank erosion"):
        u = "Before monsoon" if (people >= p["people_high"] or assets) else "Routine"
        return u, "Soil Conservation Dept", TEXT["ai"]
    if cls.startswith("Accretion"):
        return "Monitor", "Revenue / Soil Conservation Dept", TEXT["accretion"]
    if cls.startswith("New inland water"):
        return "Routine", "Water Resources / Agriculture Dept", TEXT["inland_water"]
    if cls.startswith("Vegetation loss"):
        return "Routine", "Soil Conservation Dept", TEXT["veg_loss"]
    if cls.startswith("New bare soil"):
        return "Routine", "Soil Conservation Dept", TEXT["bare_soil"]
    if cls in ("High", "Very high"):
        steep = _num(row.get("mean_slope_deg")) >= p["slope_steep_deg"]
        u = "Before monsoon" if cls == "Very high" else "Routine"
        return u, "Soil Conservation Dept", TEXT["risk_steep" if steep else "risk_gentle"]
    return "Monitor", "Soil Conservation Dept", "Field check."


def apply(hot, cfg=None):
    """Add urgency, lead_agency and recommended_action columns to the site table."""
    if hot is None or len(hot) == 0:
        return hot
    p = {**DEFAULTS, **(cfg or {}).get("interventions", {})}
    rec = [recommend(r, p) for r in hot.to_dict("records")]
    out = hot.copy()
    out["urgency"] = [r[0] for r in rec]
    out["lead_agency"] = [r[1] for r in rec]
    out["recommended_action"] = [r[2] for r in rec]
    return out


def summary(hot):
    if hot is None or len(hot) == 0:
        return {}
    c = hot["urgency"].value_counts().to_dict()
    return {u: int(c.get(u, 0)) for u in URGENCY_ORDER}
