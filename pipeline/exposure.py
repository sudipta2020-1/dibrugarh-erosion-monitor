"""
Exposure: what is at stake around each priority site.

Static layers (built once by prepare_inputs.py --only exposure, kept in data/):
  worldcover_built_frac.tif      share of built-up land per 100 m cell (ESA WorldCover 2021)
  worldcover_crop_frac.tif       share of cropland per 100 m cell (ESA WorldCover 2021)
  population_worldpop.tif        people per cell, 2020 (WorldPop)
  osm_roads.geojson              roads and railways (OpenStreetMap)
  osm_embankments.geojson        embankments and dykes (OpenStreetMap)
  osm_facilities.geojson         schools, colleges, hospitals and clinics (OpenStreetMap)
  osm_places.geojson             named villages, towns and hamlets (OpenStreetMap)

For each candidate site, a buffer (default 500 m) around the patch is used to
count people, built-up and cropland area, road and embankment length and
facilities, and the nearest named place is found. These are combined into an
exposure index between 0 and 1, and the priority of a site is

    priority = hazard x (a + (1 - a) x exposure),   a = 0.3 by default

so a site with nothing at stake keeps 30% of its hazard score, and a site next to
a village or road rises to the top of the list.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data")
DEFAULT_SAT = {"population": 2000, "built_ha": 10, "crop_ha": 40,
               "road_km": 3, "embankment_km": 1, "facilities": 2}
DEFAULT_W = {"population": 0.35, "built_ha": 0.15, "crop_ha": 0.15,
             "road_km": 0.1, "embankment_km": 0.1, "facilities": 0.15}


def _raster_points(path, crs):
    """Non-zero raster cells as points (cell centre, value) in the analysis CRS."""
    import geopandas as gpd
    import rasterio
    if not Path(path).exists():
        return None
    with rasterio.open(path) as src:
        a = src.read(1, masked=True).filled(0).astype("float64")
        rr, cc = np.nonzero(a > 0)
        xs, ys = rasterio.transform.xy(src.transform, rr, cc)
        g = gpd.GeoDataFrame({"v": a[rr, cc]}, geometry=gpd.points_from_xy(xs, ys), crs=src.crs)
        cell_ha = None
        if src.crs and src.crs.is_projected:
            cell_ha = abs(src.transform.a * src.transform.e) / 1e4
    g = g.to_crs(crs)
    g.attrs["cell_ha"] = cell_ha
    return g


def _vector(path, crs):
    import geopandas as gpd
    if not Path(path).exists():
        return None
    g = gpd.read_file(path)
    return None if g.empty else g.to_crs(crs)


def load(crs):
    """Load all exposure layers that exist. Missing layers are skipped."""
    return {"population": _raster_points(DATA / "population_worldpop.tif", crs),
            "built": _raster_points(DATA / "worldcover_built_frac.tif", crs),
            "crop": _raster_points(DATA / "worldcover_crop_frac.tif", crs),
            "roads": _vector(DATA / "osm_roads.geojson", crs),
            "embankments": _vector(DATA / "osm_embankments.geojson", crs),
            "facilities": _vector(DATA / "osm_facilities.geojson", crs),
            "places": _vector(DATA / "osm_places.geojson", crs)}


def available(ex):
    return [k for k, v in ex.items() if v is not None]


def _sum_points(pts, buffers, cell_ha=None):
    import geopandas as gpd
    if pts is None:
        return np.full(len(buffers), np.nan)
    j = gpd.sjoin(pts[["v", "geometry"]], gpd.GeoDataFrame(geometry=buffers.values, crs=pts.crs)
                  .reset_index(names="bid"), predicate="within")
    s = j.groupby("bid")["v"].sum().reindex(range(len(buffers))).fillna(0).values
    return s * cell_ha if cell_ha else s


def _length_km(lines, buffers):
    if lines is None:
        return np.full(len(buffers), np.nan)
    out = []
    for b in buffers.values:
        idx = lines.sindex.query(b, predicate="intersects")
        out.append(float(lines.geometry.iloc[idx].intersection(b).length.sum()) / 1000 if len(idx) else 0.0)
    return np.array(out)


def _count(points, buffers):
    if points is None:
        return np.full(len(buffers), np.nan)
    return np.array([len(points.sindex.query(b, predicate="intersects")) for b in buffers.values], float)


def site_exposure(sites, ex, cfg):
    """Add exposure columns and an exposure index to a GeoDataFrame of sites (UTM)."""
    ec = cfg.get("exposure", {})
    buf_m = float(ec.get("buffer_m", 500))
    sat = {**DEFAULT_SAT, **ec.get("saturation", {})}
    w = {**DEFAULT_W, **ec.get("weights", {})}
    buffers = sites.geometry.buffer(buf_m)
    built = ex["built"]
    crop = ex["crop"]
    cols = {
        "population": _sum_points(ex["population"], buffers),
        "built_ha": _sum_points(built, buffers, built.attrs.get("cell_ha") if built is not None else None),
        "crop_ha": _sum_points(crop, buffers, crop.attrs.get("cell_ha") if crop is not None else None),
        "road_km": _length_km(ex["roads"], buffers),
        "embankment_km": _length_km(ex["embankments"], buffers),
        "facilities": _count(ex["facilities"], buffers),
    }
    out = sites.copy()
    for k, v in cols.items():
        out[k] = np.round(v, 2 if k.endswith(("_ha", "_km")) else 0)
    # Exposure index over the layers that are available
    num = np.zeros(len(out))
    den = 0.0
    for k, v in cols.items():
        if np.all(np.isnan(v)):
            continue
        num += w[k] * np.clip(np.nan_to_num(v) / sat[k], 0, 1)
        den += w[k]
    out["exposure_index"] = np.round(num / den, 3) if den else np.nan
    # Nearest named place
    pl = ex["places"]
    if pl is not None and len(pl):
        cent = out.geometry.centroid
        idx = pl.sindex.nearest(cent, return_all=False)[1]
        near = pl.iloc[idx]
        out["nearest_place"] = near["name"].values
        out["nearest_place_km"] = np.round(cent.distance(near.geometry, align=False).values / 1000, 1)
    return out


def people_near(class_mask_da, ex, buf_m=500):
    """People living within buf_m of any pixel of a class mask (no double counting)."""
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union
    if ex.get("population") is None:
        return None
    a = np.asarray(class_mask_da.values).astype("uint8")
    if not a.any():
        return 0
    geoms = [shape(g) for g, v in shapes(a, mask=a > 0, transform=class_mask_da.rio.transform())]
    zone = unary_union(geoms).buffer(buf_m)
    pts = ex["population"]
    idx = pts.sindex.query(zone, predicate="intersects")
    return int(round(float(pts.iloc[idx]["v"].sum())))


# ------------------------------------------------------------------ preparation
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]


def _overpass(query, rounds=4):
    """POST a query to the Overpass mirrors. Shared CI runners are often rate
    limited (HTTP 429), so each round tries every mirror and then waits longer."""
    import time
    import requests
    last = None
    for k in range(rounds):
        for url in OVERPASS:
            try:
                r = requests.post(url, data={"data": query}, timeout=300,
                                  headers={"User-Agent": "dibrugarh-erosion-monitor/1.0 (research prototype)"})
                r.raise_for_status()
                return r.json()
            except Exception as e:  # try the next mirror
                last = f"{url.split('/')[2]}: {e}"
        wait = 30 * (k + 1)
        print(f"    Overpass busy ({last}); retrying in {wait} s")
        time.sleep(wait)
    raise RuntimeError(f"Overpass not reachable ({last})")


def _osm_to_gdf(js, kind):
    import geopandas as gpd
    from shapely.geometry import LineString, Point
    recs = []
    for el in js.get("elements", []):
        tags = el.get("tags", {})
        if kind == "line" and el["type"] == "way" and "geometry" in el and len(el["geometry"]) > 1:
            g = LineString([(p["lon"], p["lat"]) for p in el["geometry"]])
        elif kind == "point":
            if el["type"] == "node":
                g = Point(el["lon"], el["lat"])
            elif "center" in el:
                g = Point(el["center"]["lon"], el["center"]["lat"])
            else:
                continue
        else:
            continue
        recs.append({"name": tags.get("name", ""), "type": tags.get("highway") or tags.get("railway")
                     or tags.get("amenity") or tags.get("place") or tags.get("man_made")
                     or ("embankment" if tags.get("embankment") else ""), "geometry": g})
    return gpd.GeoDataFrame(recs, geometry="geometry", crs="EPSG:4326")


def prepare(bbox, gdf):
    """Download and save all exposure layers. Returns a report dict."""
    import rasterio
    rep = {}
    s, w, n, e = bbox[1] - 0.02, bbox[0] - 0.02, bbox[3] + 0.02, bbox[2] + 0.02
    b = f"({s},{w},{n},{e})"

    # OpenStreetMap
    queries = {
        "osm_roads": (f'[out:json][timeout:240];(way["highway"~"^(motorway|trunk|primary|secondary|tertiary|'
                      f'unclassified|residential)$"]{b};way["railway"="rail"]{b};);out geom;', "line"),
        "osm_embankments": (f'[out:json][timeout:240];(way["man_made"="dyke"]{b};way["embankment"="yes"]{b};'
                            f'way["man_made"="embankment"]{b};);out geom;', "line"),
        "osm_facilities": (f'[out:json][timeout:240];(nwr["amenity"~"^(school|college|university|hospital|'
                           f'clinic|doctors)$"]{b};nwr["healthcare"]{b};);out center;', "point"),
        "osm_places": (f'[out:json][timeout:240];node["place"~"^(city|town|village|hamlet|suburb)$"]["name"]{b};'
                       f'out;', "point"),
    }
    for name, (q, kind) in queries.items():
        try:
            g = _osm_to_gdf(_overpass(q), kind)
            g.to_file(DATA / f"{name}.geojson", driver="GeoJSON")
            rep[name] = {"features": int(len(g))}
            print(f"  {name}: {len(g)} features")
            import time
            time.sleep(10)                      # be polite to the shared Overpass servers
        except Exception as ex:
            rep[name] = {"error": str(ex)}
            print(f"  {name} FAILED: {ex}")

    # ESA WorldCover 2021 (10 m) from Planetary Computer -> 100 m shares
    try:
        import planetary_computer
        import pystac_client
        from odc.stac import load as stac_load
        cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                        modifier=planetary_computer.sign_inplace)
        items = cat.search(collections=["esa-worldcover"], bbox=bbox).item_collection()
        items = [i for i in items if "2021" in i.id] or list(items)
        ds = stac_load(items, bands=["map"], bbox=bbox, crs="EPSG:32646", resolution=10,
                       chunks={"x": 2048, "y": 2048}, groupby="solar_day")
        m = ds["map"].max("time")
        for code, fname in [(50, "worldcover_built_frac.tif"), (40, "worldcover_crop_frac.tif")]:
            frac = (m == code).astype("float32").coarsen(x=10, y=10, boundary="trim").mean().compute()
            frac.rio.write_crs("EPSG:32646").rio.write_nodata(-1).rio.to_raster(DATA / fname, compress="deflate")
        rep["worldcover"] = {"source": "ESA WorldCover 2021 v200, 10 m, aggregated to 100 m shares",
                             "items": len(items)}
        print("  WorldCover: built-up and cropland shares written")
    except Exception as ex:
        rep["worldcover"] = {"error": str(ex)}
        print("  WorldCover FAILED:", ex)

    # WorldPop 2020 population, 100 m, constrained to settled areas, UN-adjusted
    names = ["GIS/Population/Global_2000_2020_Constrained/2020/BSGM/IND/ind_ppp_2020_UNadj_constrained.tif",
             "GIS/Population/Global_2000_2020_Constrained/2020/BSGM/IND/ind_ppp_2020_constrained.tif"]
    hosts = ["https://data.worldpop.org/", "https://worldpop-public-data.soton.ac.uk/"]
    rep["population"] = {"error": "not attempted"}
    for name in names:
        for host in hosts:
            url = host + name
            try:
                total = _worldpop_window(url, (w, s, e, n))
                rep["population"] = {"source": "WorldPop 2020, 100 m, " + name.split("/")[-1],
                                     "people_in_window": round(total)}
                print(f"  WorldPop: {round(total)} people in the district window ({name.split('/')[-1]})")
                break
            except Exception as ex:
                rep["population"] = {"error": str(ex)}
                print("  WorldPop source failed:", url, ex)
        if "source" in rep["population"]:
            break
    return rep


def _worldpop_window(url, bounds):
    """Cut the district window out of a national WorldPop GeoTIFF. Reads only the
    window over HTTP when the server allows range requests; otherwise downloads
    the whole file once to a temporary folder and deletes it afterwards."""
    import shutil
    import tempfile
    import rasterio
    import requests
    from rasterio.windows import from_bounds

    def cut(src_path):
        with rasterio.open(src_path) as src:
            win = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
            a = src.read(1, window=win, masked=True).filled(0).astype("float32")
            a[a < 0] = 0
            prof = src.profile.copy()
            prof.update(height=a.shape[0], width=a.shape[1], transform=src.window_transform(win),
                        driver="GTiff", compress="deflate", dtype="float32", nodata=-1, count=1)
            for k in ("blockxsize", "blockysize", "tiled"):
                prof.pop(k, None)
        with rasterio.open(DATA / "population_worldpop.tif", "w", **prof) as dst:
            dst.write(a, 1)
        return float(a.sum())

    try:
        return cut("/vsicurl/" + url)
    except Exception as e:
        print(f"    range read not possible ({e}); downloading the whole file")
    tmp = Path(tempfile.mkdtemp())
    try:
        f = tmp / url.split("/")[-1]
        with requests.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(f, "wb") as out:
                for chunk in r.iter_content(chunk_size=1 << 22):
                    out.write(chunk)
        print(f"    downloaded {f.stat().st_size / 1e6:.0f} MB")
        return cut(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
