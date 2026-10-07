"""Rule-based classification (version 0).

Two methods, chosen by which data the run has:
- ortho_dhm: NDVI from the CIR orthophoto (12.5 cm) and object height from DHM. Main method.
- sentinel2: NDVI from Sentinel-2 (10 m) plus ESA WorldCover. Coarse fallback when there is no token.
OSM buildings are always used as a building mask when available.
"""

import numpy as np
from scipy.ndimage import median_filter

from .classes import BELAEGNING, BUSKE, BYGNING, GRAES, TRAEER, UKENDT

DEFAULTS = {
    "ortho_dhm": {"ndvi_vegetation": 0.10, "hoejde_busk_m": 0.5, "hoejde_trae_m": 3.0,
                  "hoejde_bygning_m": 2.5, "median_filter_px": 5},
    "sentinel2": {"ndvi_vegetation": 0.45, "median_filter_px": 3},
}


def ndvi_from_cir(cir: np.ndarray) -> np.ndarray:
    nir = cir[..., 0].astype("float32")
    red = cir[..., 1].astype("float32")
    return (nir - red) / np.maximum(nir + red, 1.0)


def ortho_dhm(ndvi: np.ndarray, ndsm: np.ndarray, osm_buildings: np.ndarray | None, p: dict) -> np.ndarray:
    veg = ndvi > p["ndvi_vegetation"]
    out = np.full(ndvi.shape, BELAEGNING, dtype="uint8")
    out[veg & (ndsm <= p["hoejde_busk_m"])] = GRAES
    out[veg & (ndsm > p["hoejde_busk_m"]) & (ndsm <= p["hoejde_trae_m"])] = BUSKE
    out[veg & (ndsm > p["hoejde_trae_m"])] = TRAEER
    if osm_buildings is not None:
        # Footprints are authoritative. The spring orthophoto is leaf-off, so tall objects without
        # vegetation signal outside a footprint are bare deciduous trees. Low ones (cars, fences) stay paving.
        out[~veg & (ndsm > p["hoejde_trae_m"])] = TRAEER
        out[osm_buildings] = BYGNING
    else:
        out[~veg & (ndsm > p["hoejde_bygning_m"])] = BYGNING
    out[np.isnan(ndsm) | np.isnan(ndvi)] = UKENDT
    if p["median_filter_px"] > 1:
        out = median_filter(out, size=p["median_filter_px"])
    return out


# ESA WorldCover classes
WC_TREE, WC_SHRUB, WC_GRASS, WC_CROP, WC_BUILT, WC_BARE = 10, 20, 30, 40, 50, 60


def sentinel2(ndvi: np.ndarray, worldcover: np.ndarray | None, osm_buildings: np.ndarray | None,
              p: dict) -> np.ndarray:
    veg = ndvi > p["ndvi_vegetation"]
    out = np.full(ndvi.shape, BELAEGNING, dtype="uint8")
    out[veg] = GRAES
    if worldcover is not None:
        out[veg & (worldcover == WC_TREE)] = TRAEER
        out[veg & (worldcover == WC_SHRUB)] = BUSKE
    if osm_buildings is not None:
        out[osm_buildings] = BYGNING
    out[np.isnan(ndvi)] = UKENDT
    if p["median_filter_px"] > 1:
        out = median_filter(out, size=p["median_filter_px"])
    return out
