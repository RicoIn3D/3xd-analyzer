"""OpenStreetMap via Overpass: buildings, roads, parking, grass and trees as a class raster (ODbL)."""

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


def osm(grid: Grid) -> dict:
    lon0, lat0, lon1, lat1 = grid.bbox_wgs84()
    bb = f"{lat0},{lon0},{lat1},{lon1}"
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
    status = []
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": q}, timeout=120,
                              headers={"User-Agent": "Analyzier/0.1 (3XD ApS)"})
        except requests.RequestException as e:
            status.append(f"{url}: {type(e).__name__}")
            continue
        if r.status_code == 200:
            break
        status.append(f"{url}: HTTP {r.status_code}")
    else:
        raise SourceUnavailable("Overpass: " + "; ".join(status))
    elements = r.json().get("elements", [])

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
            "antal_objekter": len(features)}
