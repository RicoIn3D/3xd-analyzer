"""Skråfotos (oblique aerial photos) from Dataforsyningen's STAC API.

Search needs DATAFORSYNINGEN_TOKEN. The images are Cloud Optimized GeoTIFFs on a public CDN and are
read with range requests. Each item carries camera parameters (pers:*), so a map coordinate can be
projected into the image with the collinearity equations. Used for visual control, not classification.
"""

import os
import warnings
from pathlib import Path

import numpy as np
import rasterio
import requests
from PIL import Image, ImageDraw
from pyproj import Transformer
from rasterio.windows import Window
from shapely.geometry import MultiPolygon

from ..grid import CRS
from . import SourceUnavailable

STAC_URL = "https://api.dataforsyningen.dk/rest/skraafoto_api/v1.0"
DIRECTIONS = ("nadir", "north", "south", "east", "west")
DIRECTION_NAMES = {"nadir": "Lodret", "north": "Mod nord", "south": "Mod syd", "east": "Mod øst", "west": "Mod vest"}
MARGIN_PX = 250
MAX_SIDE_PX = 1600

# The oblique images have no geotransform; we project with the camera model instead
warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

_to_wgs84 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)


def project(props: dict, x: float, y: float, z: float) -> tuple[float, float]:
    """Map coordinate (EPSG:25832, height in DVR90) to image column/row (full resolution)."""
    r = np.array(props["pers:rotation_matrix"]).reshape(3, 3)
    io = props["pers:interior_orientation"]
    f = io["focal_length"]
    pixel = io["pixel_spacing"][0]
    ppx, ppy = io["principal_point_offset"]
    cols, rows = io["sensor_array_dimensions"]
    u = r @ (np.array([x, y, z]) - np.array(props["pers:perspective_center"]))
    xi = ppx - f * u[0] / u[2]
    yi = ppy - f * u[1] / u[2]
    return xi / pixel + cols / 2, rows / 2 - yi / pixel


def _rings(geom):
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    return [list(p.exterior.coords) for p in polys]


def skraafoto(area, ground_z: float, out_dir: Path, year: int = 2025) -> dict:
    """Save one crop per direction with the property outline drawn on it. Returns metadata per image."""
    token = os.environ.get("DATAFORSYNINGEN_TOKEN", "").strip()
    if not token:
        raise SourceUnavailable("DATAFORSYNINGEN_TOKEN mangler i .env")
    x0, y0, x1, y1 = area.bounds
    lon0, lat0 = _to_wgs84.transform(x0, y0)
    lon1, lat1 = _to_wgs84.transform(x1, y1)
    r = requests.get(f"{STAC_URL}/collections/skraafotos{year}/items", headers={"token": token}, timeout=60,
                     params={"bbox": f"{lon0},{lat0},{lon1},{lat1}", "limit": 100})
    if r.status_code != 200:
        raise SourceUnavailable(f"Skråfoto-API: HTTP {r.status_code} {r.text[:200]}")
    items = r.json().get("features", [])
    if not items:
        raise SourceUnavailable(f"Ingen skråfotos fra {year} over ejendommen")

    # Per direction, pick the image where the property lies closest to the image centre
    cx, cy = area.centroid.x, area.centroid.y
    best = {}
    for it in items:
        p = it["properties"]
        cols, rows = p["pers:interior_orientation"]["sensor_array_dimensions"]
        c, rw = project(p, cx, cy, ground_z)
        dist = np.hypot(c - cols / 2, rw - rows / 2)
        if p["direction"] not in best or dist < best[p["direction"]][0]:
            best[p["direction"]] = (dist, it)

    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for direction in DIRECTIONS:
        if direction not in best:
            continue
        it = best[direction][1]
        p = it["properties"]
        rings = [[project(p, x, y, ground_z) for x, y, *_ in ring] for ring in _rings(area)]
        cs = [c for ring in rings for c, _ in ring]
        rs = [rw for ring in rings for _, rw in ring]
        c0, r0 = int(min(cs)) - MARGIN_PX, int(min(rs)) - MARGIN_PX
        w, h = int(max(cs) - min(cs)) + 2 * MARGIN_PX, int(max(rs) - min(rs)) + 2 * MARGIN_PX
        with rasterio.open(it["assets"]["data"]["href"]) as ds:
            c0, r0 = max(c0, 0), max(r0, 0)
            w, h = min(w, ds.width - c0), min(h, ds.height - r0)
            data = ds.read([1, 2, 3], window=Window(c0, r0, w, h))
        img = Image.fromarray(np.moveaxis(data, 0, -1))
        draw = ImageDraw.Draw(img)
        for ring in rings:
            draw.line([(c - c0, rw - r0) for c, rw in ring], fill=(255, 0, 200), width=5)
        img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
        name = f"{direction}.jpg"
        img.save(out_dir / name, quality=85)
        images.append({"retning": direction, "navn": DIRECTION_NAMES[direction], "fil": name, "id": it["id"],
                       "dato": p["datetime"][:10]})
    return {"billeder": images, "år": year}
