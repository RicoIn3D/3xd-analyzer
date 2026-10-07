"""OpenStreetMap via Overpass: buildings, roads, parking, grass and trees as a class raster (ODbL)."""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import requests
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.ops import transform

from ..classes import BELAEGNING, BUSKE, BYGNING, GRAES, TRAEER
from ..grid import CRS, Grid
from . import SourceUnavailable

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
# Successful answers are cached per query. A fresh entry is reused; a stale one is the fallback when Overpass is down.
CACHE_DIR = Path(__file__).resolve().parents[2] / "cache" / "osm"
CACHE_MAX_AGE_DAYS = 30
# One round tries every server. Pause in seconds after each round; 0 = no pause after the last round.
RETRY_PAUSES_S = (10, 30, 0)
REQUEST_TIMEOUT_S = 45
_to_utm = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform

UNPAVED = {"grass", "dirt", "earth", "ground", "unpaved", "grass_paver", "sand", "mud"}
# Half-width in metres when a road is drawn as a line
ROAD_HALF_WIDTH = {"primary": 4.0, "secondary": 3.5, "tertiary": 3.0, "residential": 2.75, "service": 2.0,
                   "unclassified": 2.5, "living_street": 2.5, "pedestrian": 2.0}
PATH_HALF_WIDTH = 0.75


def _classify(tags: dict, closed: bool):
    """Return (class, priority, half_width). Higher priority is drawn on top. None means ignore."""
    if "building" in tags:
        return BYGNING, 6, None
    if tags.get("natural") == "tree":
        return TRAEER, 5, 3.0
    if tags.get("natural") == "tree_row":
        return TRAEER, 5, 2.5
    hw = tags.get("highway")
    if hw:
        if tags.get("surface") in UNPAVED or hw in ("track", "proposed", "construction"):
            return None
        if closed and tags.get("area") == "yes":
            return BELAEGNING, 4, None
        return BELAEGNING, 4, ROAD_HALF_WIDTH.get(hw, PATH_HALF_WIDTH)
    if tags.get("amenity") == "parking" and closed:
        return BELAEGNING, 4, None
    if tags.get("natural") in ("wood",) or tags.get("landuse") == "forest":
        return TRAEER, 3, None
    if tags.get("natural") in ("scrub", "heath"):
        return BUSKE, 3, None
    if tags.get("landuse") in ("grass", "meadow", "village_green") and closed:
        return GRAES, 2, None
    if tags.get("leisure") in ("park", "garden") and closed:
        return GRAES, 1, None
    return None


def _overpass(q: str) -> tuple[dict, dict]:
    """Query Overpass with retries across servers. Cache successful answers on disk.

    Returns (answer, info). info says whether the answer is live or from cache and lists failed attempts.
    Falls back to a stale cache entry when all servers fail.
    """
    key = hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]
    cache_file = CACHE_DIR / f"{key}.json"
    attempts = []

    if cache_file.exists():
        age_days = (time.time() - cache_file.stat().st_mtime) / 86400
        if age_days < CACHE_MAX_AGE_DAYS:
            return json.loads(cache_file.read_text(encoding="utf-8")), {
                "kilde": "cache", "alder_dage": round(max(age_days, 0.0), 1), "forsøg": attempts}

    for round_no, pause in enumerate(RETRY_PAUSES_S, start=1):
        for url in OVERPASS_URLS:
            started = time.monotonic()
            try:
                r = requests.post(url, data={"data": q}, timeout=REQUEST_TIMEOUT_S,
                                  headers={"User-Agent": "Analyzer/0.2 (3XD ApS)"})
                result = f"HTTP {r.status_code}"
                if r.status_code == 200:
                    answer = r.json()
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(answer), encoding="utf-8")
                    return answer, {"kilde": "live", "server": url, "forsøg": attempts}
            except (requests.RequestException, ValueError) as e:
                result = type(e).__name__
            attempts.append({"runde": round_no, "server": url, "resultat": result,
                             "sekunder": round(time.monotonic() - started, 1)})
        if pause:
            time.sleep(pause)

    if cache_file.exists():
        age_days = (time.time() - cache_file.stat().st_mtime) / 86400
        return json.loads(cache_file.read_text(encoding="utf-8")), {
            "kilde": "gammel cache", "alder_dage": round(max(age_days, 0.0), 1), "forsøg": attempts}
    raise SourceUnavailable("Overpass: alle forsøg fejlede: " +
                            "; ".join(f"{a['server'].split('/')[2]} {a['resultat']}" for a in attempts))


def osm(grid: Grid) -> dict:
    lon0, lat0, lon1, lat1 = grid.bbox_wgs84()
    bb = f"{lat0:.5f},{lon0:.5f},{lat1:.5f},{lon1:.5f}"
    q = f"""[out:json][timeout:60];
(
  way["building"]({bb});
  way["highway"]({bb});
  way["amenity"="parking"]({bb});
  way["landuse"~"^(grass|meadow|village_green|forest)$"]({bb});
  way["leisure"~"^(park|garden)$"]({bb});
  way["natural"~"^(wood|scrub|heath|tree_row)$"]({bb});
  node["natural"="tree"]({bb});
);
out geom;"""
    answer, info = _overpass(q)
    elements = answer.get("elements", [])

    shapes, features = [], []
    for el in elements:
        tags = el.get("tags", {})
        if el["type"] == "node":
            geom = Point(el["lon"], el["lat"])
            closed = False
        else:
            pts = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
            if len(pts) < 2:
                continue
            closed = len(pts) >= 4 and pts[0] == pts[-1]
            geom = Polygon(pts) if closed and "highway" not in tags else LineString(pts)
            if closed and tags.get("area") == "yes":
                geom = Polygon(pts)
        res = _classify(tags, closed)
        if res is None:
            continue
        cls, prio, half_width = res
        utm = transform(_to_utm, geom)
        if half_width is not None or not isinstance(utm, Polygon):
            utm = utm.buffer(half_width or 1.0)
        if utm.is_empty:
            continue
        shapes.append((prio, utm, cls))
        features.append({"type": "Feature", "geometry": mapping(geom),
                         "properties": {"klasse": cls, "osm_id": f"{el['type']}/{el['id']}", **tags}})

    shapes.sort(key=lambda s: s[0])
    raster = np.zeros(grid.shape, dtype="uint8")
    if shapes:
        raster = rasterize(((g, c) for _, g, c in shapes), out_shape=grid.shape,
                           transform=grid.transform, fill=0, dtype="uint8")
    return {"klasser": raster, "geojson": {"type": "FeatureCollection", "features": features},
            "antal_objekter": len(features), "hentning": info}
