"""Dataforsyningen: GeoDanmark orthophoto (RGB + CIR) and Danmarks Højdemodel (DHM).

Needs DATAFORSYNINGEN_TOKEN. GetCapabilities works without a token, data requests do not.
"""

import io
import os

import numpy as np
import requests
from PIL import Image
from rasterio.io import MemoryFile

from ..grid import CRS, Grid
from . import SourceUnavailable

ORTO_URL = "https://api.dataforsyningen.dk/orto_foraar_DAF"
DHM_URL = "https://api.dataforsyningen.dk/dhm_wcs_DAF"
MAX_PIXELS = 4000
# Layer resolutions, best first. 10 cm only exists where the municipality ordered it.
ORTO_RESOLUTIONS = ("10cm", "12_5cm")
# Share of empty pixels above which a layer counts as not covering the area. The WMS fills areas
# without data with pure white (seen on the 10 cm layers), older layers with pure black.
MAX_EMPTY_SHARE = 0.005


def _token() -> str:
    token = os.environ.get("DATAFORSYNINGEN_TOKEN", "").strip()
    if not token:
        raise SourceUnavailable("DATAFORSYNINGEN_TOKEN mangler i .env")
    return token


def _check_size(grid: Grid) -> None:
    if max(grid.width, grid.height) > MAX_PIXELS:
        raise SourceUnavailable(f"Området er for stort til ét WMS-kald ({grid.width}x{grid.height} px)")


def orthophoto(grid: Grid, year: int, cir: bool = False) -> dict:
    """Return {"billede": (H, W, 3) uint8 image, "lag": layer name} for the best layer covering the grid.

    CIR channels are (NIR, red, green). Tries 10 cm first and falls back to 12.5 cm.
    """
    _check_size(grid)
    errors = []
    for resolution in ORTO_RESOLUTIONS:
        layer = f"geodanmark_{year}_{resolution}" + ("_cir" if cir else "")
        try:
            img = _orthophoto_layer(grid, layer)
        except SourceUnavailable as e:
            errors.append(str(e))
            continue
        empty = float(((img.max(axis=2) == 0) | (img.min(axis=2) == 255)).mean())
        if empty > MAX_EMPTY_SHARE:
            errors.append(f"{layer}: {empty:.1%} af området uden data")
            continue
        return {"billede": img, "lag": layer}
    raise SourceUnavailable("; ".join(errors))


def _orthophoto_layer(grid: Grid, layer: str) -> np.ndarray:
    params = {
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": layer, "styles": "", "srs": CRS,
        "bbox": ",".join(f"{v:.2f}" for v in grid.bbox),
        "width": grid.width, "height": grid.height,
        "format": "image/png", "token": _token(),
    }
    r = requests.get(ORTO_URL, params=params, timeout=180)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise SourceUnavailable(f"{layer}: HTTP {r.status_code} {r.text[:200]}")
    img = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
    if img.max() == 0:
        raise SourceUnavailable(f"{layer}: tomt billede (ingen dækning)")
    return img


def dhm(grid: Grid, coverage: str) -> np.ndarray:
    """Return elevation in metres. coverage is 'dhm_terraen' or 'dhm_overflade'."""
    _check_size(grid)
    params = {
        "service": "WCS", "version": "1.0.0", "request": "GetCoverage",
        "coverage": coverage, "crs": CRS,
        "bbox": ",".join(f"{v:.2f}" for v in grid.bbox),
        "width": grid.width, "height": grid.height,
        "format": "GTiff", "token": _token(),
    }
    r = requests.get(DHM_URL, params=params, timeout=180)
    if r.status_code != 200 or b"Exception" in r.content[:500]:
        raise SourceUnavailable(f"{coverage}: HTTP {r.status_code} {r.text[:200]}")
    with MemoryFile(r.content) as mem, mem.open() as ds:
        data = ds.read(1).astype("float32")
        if ds.nodata is not None:
            data[data == ds.nodata] = np.nan
    if data.shape != grid.shape:
        raise SourceUnavailable(f"{coverage}: forkert størrelse {data.shape}, forventet {grid.shape}")
    return data
