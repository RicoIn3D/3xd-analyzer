"""Rule-based classification (version 0).

Two methods, chosen by which data the run has:
- ortho_dhm: NDVI from the CIR orthophoto (12.5 cm) and object height from DHM. Main method.
  When the DHM point cloud is available, its vegetation classes decide trees and shrubs. That fixes
  leaf-off trees in the spring orthophoto, which have a low NDVI.
- sentinel2: NDVI from Sentinel-2 (10 m) plus ESA WorldCover. Coarse fallback when there is no token.
OSM buildings are always used as a building mask when available.
"""

import numpy as np
from scipy.ndimage import median_filter, uniform_filter

from .classes import BELAEGNING, BUSKE, BYGNING, GRAES, TRAEER, UKENDT

DEFAULTS = {
    "ortho_dhm": {"ndvi_vegetation": 0.10, "hoejde_busk_m": 0.5, "hoejde_trae_m": 3.0,
                  "hoejde_bygning_m": 2.5, "median_filter_px": 5,
                  "punktsky_andel_trae": 0.25, "punktsky_andel_busk": 0.25, "punktsky_naboskab_px": 5,
                  "punktsky_busk_min_ndvi": 0.0},
    "sentinel2": {"ndvi_vegetation": 0.45, "median_filter_px": 3},
}


def ndvi_from_cir(cir: np.ndarray) -> np.ndarray:
    nir = cir[..., 0].astype("float32")
    red = cir[..., 1].astype("float32")
    return (nir - red) / np.maximum(nir + red, 1.0)


def ortho_dhm(ndvi: np.ndarray, ndsm: np.ndarray, osm_buildings: np.ndarray | None, p: dict,
              lidar: dict | None = None) -> np.ndarray:
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
    if lidar is not None:
        _apply_lidar(out, ndvi, veg, lidar, p)
    out[np.isnan(ndsm) | np.isnan(ndvi)] = UKENDT
    if p["median_filter_px"] > 1:
        out = median_filter(out, size=p["median_filter_px"])
    return out


def _apply_lidar(out: np.ndarray, ndvi: np.ndarray, veg: np.ndarray, counts: dict, p: dict) -> None:
    """Let the point cloud's vegetation classes decide trees and shrubs (outside buildings)."""
    size = p["punktsky_naboskab_px"]
    share = {c: uniform_filter(v.astype("float32"), size=size) for c, v in counts.items()}
    total = sum(share.values())
    has = total > 0
    high = np.where(has, share[5] / np.maximum(total, 1e-6), 0)
    medium = np.where(has, share[4] / np.maximum(total, 1e-6), 0)
    free = out != BYGNING
    tree = free & (high >= p["punktsky_andel_trae"])
    # Cars and other objects in the 2023 scan are often classified as medium vegetation. A shrub must
    # also show some vegetation signal in the orthophoto (local mean NDVI).
    green = uniform_filter(np.nan_to_num(ndvi, nan=-1), size=size) > p["punktsky_busk_min_ndvi"]
    shrub = free & ~tree & (medium >= p["punktsky_andel_busk"]) & green
    # Called tree or shrub from height alone, but the point cloud sees no tall vegetation: fall back to NDVI
    no_tall = free & has & ~tree & ~shrub & np.isin(out, (TRAEER, BUSKE)) & (high + medium < 0.1)
    out[no_tall & veg] = GRAES
    out[no_tall & ~veg] = BELAEGNING
    out[tree] = TRAEER
    out[shrub] = BUSKE


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
