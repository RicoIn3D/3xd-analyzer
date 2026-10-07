"""Datafordeler: property area (MAT, GraphQL), building attributes (BBR, GraphQL) and GeoDanmark vector data (WFS).

GraphQL schemas: https://datafordeler.dk/GraphQLSchema/<REGISTER>.graphql
Every GraphQL entity query must filter on id_lokalId (or datafordelerRowId) and pass
virkningstid or registreringstid. Spatial queries are therefore done through WFS.
"""

import datetime as dt
import os
import re
import xml.etree.ElementTree as ET

import numpy as np
import requests
from rasterio.features import rasterize
from shapely import force_2d, wkt
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union
from pyproj import Transformer

from ..classes import BELAEGNING, BUSKE, BYGNING, TRAEER
from ..grid import CRS, Grid
from . import SourceUnavailable

GEODKV_WFS = "https://wfs.datafordeler.dk/GEODKV/GEODKV_WFS/1.0.0/WFS"

# For a samlet fast ejendom the lokalId is the BFE number.
SFE_QUERY = """
query ($bfe: String!, $t: DafDateTime!) {
  MAT_SamletFastEjendom(first: 5, virkningstid: $t, where: { id_lokalId: { eq: $bfe } }) {
    nodes { id_lokalId BFEnummer status geometri { wkt } }
  }
}
"""

# BBR buildings are looked up by id_lokalId, which GeoDanmark buildings carry as BBRUUID.
# Passing both virkningstid and registreringstid returns only the current version of each building.
BBR_QUERY = """
query ($ids: [String!], $t: DafDateTime!) {
  BBR_Bygning(first: 100, virkningstid: $t, registreringstid: $t, where: { id_lokalId: { in: $ids } }) {
    nodes {
      id_lokalId status byg021BygningensAnvendelse byg026Opfoerelsesaar byg027OmTilbygningsaar
      byg032YdervaeggensMateriale byg033Tagdaekningsmateriale byg041BebyggetAreal byg054AntalEtager
    }
  }
}
"""
BBR_MAX_IDS = 100  # list filters are capped at 100 values

# GeoDanmark entities used in the run: entity -> (class or None, buffer in metres for points/lines, priority)
GEODANMARK_ENTITIES = {
    "Bygning": (BYGNING, None, 6),
    "Trae": (TRAEER, 3.0, 5),
    "Traegruppe": (TRAEER, None, 5),
    "Parkering": (BELAEGNING, None, 4),
    "Plads": (BELAEGNING, None, 4),
    "KratBevoksning": (BUSKE, None, 3),
    "Skov": (TRAEER, None, 2),
    # Shown for control only, not rasterized: lines or mixed surfaces
    "Vejkant": (None, None, 0),
    "Hegn": (None, None, 0),
    "Sportsbane": (None, None, 0),
    "RekreativtOmraade": (None, None, 0),
    "Bassin": (None, None, 0),
    "Soe": (None, None, 0),
}

_to_wgs84 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform


def _api_key() -> str:
    key = os.environ.get("DATAFORDELER_API_KEY", "").strip()
    if not key:
        raise SourceUnavailable("DATAFORDELER_API_KEY mangler i .env")
    return key


def _graphql_endpoint(register: str) -> str:
    key = _api_key()
    version = os.environ.get(f"DATAFORDELER_{register}_VERSION", "").strip()
    base = os.environ.get("DATAFORDELER_GRAPHQL_BASE_URL", "https://graphql.datafordeler.dk").rstrip("/")
    if not version:
        raise SourceUnavailable(f"DATAFORDELER_{register}_VERSION mangler i .env")
    return f"{base}/{register}/{version}?apiKey={key}"


def query(register: str, gql: str, variables: dict) -> dict:
    r = requests.post(_graphql_endpoint(register), json={"query": gql, "variables": variables}, timeout=60)
    if r.status_code != 200:
        raise SourceUnavailable(f"Datafordeler {register}: HTTP {r.status_code} {r.text[:300]}")
    body = r.json()
    if body.get("errors"):
        raise SourceUnavailable(f"Datafordeler {register}: {body['errors'][0].get('message', body['errors'])}")
    return body["data"]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def property_area(bfe: str):
    """Return the samlet fast ejendom as a shapely geometry in EPSG:25832."""
    nodes = query("MAT", SFE_QUERY, {"bfe": str(bfe), "t": _now()})["MAT_SamletFastEjendom"]["nodes"]
    geoms = [wkt.loads(n["geometri"]["wkt"]) for n in nodes if n.get("geometri")]
    if not geoms:
        raise SourceUnavailable(f"MAT: ingen samlet fast ejendom med lokalId {bfe}")
    return unary_union(geoms)


def bbr_buildings(ids: list[str]) -> dict:
    """Return the current BBR record for each building id (BBRUUID), keyed by id."""
    if not ids:
        raise SourceUnavailable("Ingen GeoDanmark-bygninger med BBR-reference")
    unique = sorted(set(ids))
    result = {}
    for i in range(0, len(unique), BBR_MAX_IDS):
        batch = unique[i:i + BBR_MAX_IDS]
        for n in query("BBR", BBR_QUERY, {"ids": batch, "t": _now()})["BBR_Bygning"]["nodes"]:
            result[n["id_lokalId"]] = n
    if not result:
        raise SourceUnavailable(f"BBR: ingen af {len(unique)} bygninger fundet")
    return result


# ---------- GeoDanmark WFS ----------

def _feature_types(key: str) -> list[str]:
    r = requests.get(GEODKV_WFS, params={"service": "WFS", "version": "2.0.0", "request": "GetCapabilities",
                                         "apikey": key}, timeout=60)
    if r.status_code != 200:
        raise SourceUnavailable(f"GeoDanmark WFS GetCapabilities: HTTP {r.status_code} {r.text[:200]}")
    root = ET.fromstring(r.content)
    names = []
    for ft in root.iter():
        if ft.tag.endswith("}FeatureType"):
            names += [c.text for c in ft if c.tag.endswith("}Name") and c.text]
    return names


def _pick(types: list[str], entity: str) -> str | None:
    """Pick the current (not historical) feature type for an entity."""
    pattern = re.compile(rf"(^|:){entity}(_current)?$", re.IGNORECASE)
    matches = [t for t in types if pattern.search(t)]
    return sorted(matches, key=lambda t: "_current" not in t)[0] if matches else None


def geodanmark(grid: Grid) -> dict:
    key = _api_key()
    types = _feature_types(key)
    bbox = ",".join(f"{v:.2f}" for v in grid.bbox) + f",{CRS}"
    shapes, features, counts, missing, buildings = [], [], {}, [], []
    for entity, (cls, buf, prio) in GEODANMARK_ENTITIES.items():
        type_name = _pick(types, entity)
        if not type_name:
            missing.append(entity)
            continue
        r = requests.get(GEODKV_WFS, timeout=120, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": type_name,
            "bbox": bbox, "srsName": CRS, "outputFormat": "application/json", "count": 10000, "apikey": key})
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("application/json"):
            raise SourceUnavailable(f"GeoDanmark {type_name}: HTTP {r.status_code} {r.text[:200]}")
        items = r.json().get("features", [])
        counts[entity] = len(items)
        for f in items:
            if not f.get("geometry"):
                continue
            geom = force_2d(shape(f["geometry"]))
            if entity == "Bygning":
                buildings.append({"geom": geom, "properties": f.get("properties") or {}})
            features.append({"type": "Feature", "geometry": mapping(transform(_to_wgs84, geom)),
                             "properties": {"objekttype": entity, **{k: v for k, v in (f.get("properties") or {}).items()
                                                                     if isinstance(v, (str, int, float, bool))}}})
            if cls is None:
                continue
            if buf is not None or geom.geom_type not in ("Polygon", "MultiPolygon"):
                geom = geom.buffer(buf or 1.0)
            shapes.append((prio, geom, cls))
    if not counts:
        raise SourceUnavailable(f"GeoDanmark: ingen kendte objekttyper i WFS. Fandt: {types[:20]}")
    shapes.sort(key=lambda s: s[0])
    raster = np.zeros(grid.shape, dtype="uint8")
    if shapes:
        raster = rasterize(((g, c) for _, g, c in shapes), out_shape=grid.shape,
                           transform=grid.transform, fill=0, dtype="uint8")
    return {"klasser": raster, "geojson": {"type": "FeatureCollection", "features": features},
            "antal": counts, "mangler": missing, "bygninger": buildings}
