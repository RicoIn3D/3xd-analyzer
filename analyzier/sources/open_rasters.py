"""Keyless raster sources: Sentinel-2 L2A (Earth Search STAC on AWS) and ESA WorldCover 10 m."""

import math
import os

import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from ..grid import CRS, Grid
from . import SourceUnavailable

STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
WORLDCOVER_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)
# Sentinel-2 scene classification: 3 cloud shadow, 8/9 cloud, 10 cirrus
S2_BAD_SCL = (0, 1, 3, 8, 9, 10)

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")


def _warp(url: str, grid: Grid, resampling: Resampling) -> np.ndarray:
    with rasterio.open(url) as src, WarpedVRT(
        src, crs=CRS, transform=grid.transform, width=grid.width, height=grid.height, resampling=resampling
    ) as vrt:
        return vrt.read(1)


def _scale(asset: dict) -> tuple[float, float]:
    bands = asset.get("raster:bands") or [{}]
    return bands[0].get("scale", 1.0), bands[0].get("offset", 0.0)


def sentinel2(grid: Grid, years: list[int], max_scenes: int = 4, max_cloud: float = 10.0) -> dict:
    """Median NDVI and true-colour RGB from the clearest summer scenes (June–August)."""
    lon0, lat0, lon1, lat1 = grid.bbox_wgs84()
    items = []
    for year in years:
        body = {
            "collections": ["sentinel-2-l2a"],
            "bbox": [lon0, lat0, lon1, lat1],
            "datetime": f"{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": max_cloud}},
            "limit": 50,
        }
        r = requests.post(STAC_SEARCH, json=body, timeout=60)
        if r.status_code != 200:
            raise SourceUnavailable(f"Earth Search: HTTP {r.status_code} {r.text[:200]}")
        items += r.json().get("features", [])
    if not items:
        raise SourceUnavailable("Sentinel-2: ingen skyfrie sommerscener fundet")
    items.sort(key=lambda f: f["properties"].get("eo:cloud_cover", 100))

    ndvis, rgbs, used = [], [], []
    for item in items:
        if len(used) >= max_scenes:
            break
        a = item["assets"]
        scl = _warp(a["scl"]["href"], grid, Resampling.nearest)
        bad = np.isin(scl, S2_BAD_SCL)
        if bad.mean() > 0.2:
            continue
        bands = {}
        for name in ("red", "green", "blue", "nir"):
            s, o = _scale(a[name])
            bands[name] = _warp(a[name]["href"], grid, Resampling.bilinear).astype("float32") * s + o
        ndvi = (bands["nir"] - bands["red"]) / np.maximum(bands["nir"] + bands["red"], 1e-6)
        ndvi[bad] = np.nan
        ndvis.append(ndvi)
        rgb = np.stack([bands["red"], bands["green"], bands["blue"]], axis=-1)
        rgb[bad] = np.nan
        rgbs.append(rgb)
        used.append({"id": item["id"], "dato": item["properties"]["datetime"][:10],
                     "skydække_%": item["properties"].get("eo:cloud_cover")})
    if not used:
        raise SourceUnavailable("Sentinel-2: alle scener var skyede over området")
    rgb = np.nanmedian(np.stack(rgbs), axis=0)
    rgb8 = np.clip(rgb / 0.15 * 255, 0, 255).astype("uint8")
    return {"ndvi": np.nanmedian(np.stack(ndvis), axis=0), "rgb": rgb8, "scener": used}


def worldcover(grid: Grid) -> np.ndarray:
    lon0, lat0, _, _ = grid.bbox_wgs84()
    lat_t = math.floor(lat0 / 3) * 3
    lon_t = math.floor(lon0 / 3) * 3
    tile = f"{'N' if lat_t >= 0 else 'S'}{abs(lat_t):02d}{'E' if lon_t >= 0 else 'W'}{abs(lon_t):03d}"
    try:
        return _warp(WORLDCOVER_URL.format(tile=tile), grid, Resampling.nearest)
    except rasterio.errors.RasterioIOError as e:
        raise SourceUnavailable(f"WorldCover {tile}: {e}") from e
