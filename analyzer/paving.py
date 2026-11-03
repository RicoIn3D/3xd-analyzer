"""Paving types inside the paving class (draft, not yet checked against a reference).

Rule-based, per cell, from three signals:
- point cloud intensity of ground points, relative to the median of all ground points in the grid (so a flight
  with a different calibration still works). Asphalt reflects the laser weakly, light stone and gravel strongly.
  Unaffected by shadows.
- orthophoto brightness and warmth ((red - blue) / brightness). Gravel and sand are warm; asphalt is dark grey.
  Not used in shadow, where everything is dark and blue.
- NDVI: gravel and joints often carry a little vegetation.
The 0.25 m orthophoto cannot see joint patterns, so pavers and tiles are not told apart from concrete.
"""

import numpy as np
from PIL import Image
from scipy.ndimage import median_filter, uniform_filter

from .classes import BELAEGNING

ASFALT, LYS, GRUS, UKENDT_TYPE = 1, 2, 3, 4
TYPE_NAMES = {ASFALT: "Asfalt", LYS: "Fliser, sten eller beton", GRUS: "Grus", UKENDT_TYPE: "Ukendt belægning"}
TYPE_COLORS = {ASFALT: (70, 70, 78), LYS: (205, 160, 120), GRUS: (225, 205, 130), UKENDT_TYPE: (190, 190, 190)}

DEFAULTS = {
    "intensitet_naboskab_px": 5,      # intensity is averaged over this window (1.25 m); ground points are sparse
    "asfalt_max_intensitet": 0.95,    # relative to the median ground intensity
    "grus_min_intensitet": 1.06,
    "asfalt_max_lysstyrke": 150,      # orthophoto brightness 0-255, outside shadow
    "skygge_max_lysstyrke": 90,       # darker than this is shadow: the orthophoto colour is not used
    "grus_min_varme": -0.05,          # (red - blue) / brightness
    "grus_min_ndvi": -0.12,
    "median_filter_px": 7,
}


def ground_intensity(intensity: np.ndarray | None, p: dict = DEFAULTS) -> np.ndarray | None:
    """Mean ground intensity per cell, smoothed with gaps filled, divided by the grid's median."""
    if intensity is None:
        return None
    ok = ~np.isnan(intensity)
    if not ok.any():
        return None
    k = p["intensitet_naboskab_px"]
    weight = uniform_filter(ok.astype("float32"), k)
    smooth = uniform_filter(np.where(ok, intensity, 0).astype("float32"), k) / np.maximum(weight, 1e-6)
    smooth[weight == 0] = np.nan
    return smooth / float(np.nanmedian(intensity))


def classify(classes: np.ndarray, rgb: np.ndarray | None, ndvi: np.ndarray | None,
             intensity: np.ndarray | None, p: dict = DEFAULTS) -> np.ndarray:
    """Paving type per cell (0 where the cell is not paving)."""
    paving = classes == BELAEGNING
    out = np.zeros(classes.shape, dtype="uint8")
    rel = ground_intensity(intensity, p)
    has_i = ~np.isnan(rel) if rel is not None else np.zeros(classes.shape, bool)
    rel = np.nan_to_num(rel, nan=1.0) if rel is not None else np.ones(classes.shape)
    if rgb is not None:
        light = rgb.astype("float32").mean(axis=2)
        warm = (rgb[..., 0].astype("float32") - rgb[..., 2]) / np.maximum(light, 1)
        lit = light > p["skygge_max_lysstyrke"]
    else:
        light = warm = np.zeros(classes.shape, "float32")
        lit = np.zeros(classes.shape, bool)
    green = ndvi if ndvi is not None else np.full(classes.shape, -1.0)

    out[paving] = LYS
    asphalt = (has_i & (rel <= p["asfalt_max_intensitet"]) & (~lit | (light <= p["asfalt_max_lysstyrke"]))) | (
        ~has_i & lit & (light <= p["asfalt_max_lysstyrke"]) & (warm < p["grus_min_varme"]))
    gravel = (lit & (warm >= p["grus_min_varme"]) & (green >= p["grus_min_ndvi"])
              & (~has_i | (rel >= 1.0))) | (has_i & (rel >= p["grus_min_intensitet"]) & (~lit | (warm >= -0.15)))
    out[paving & asphalt] = ASFALT
    out[paving & gravel] = GRUS
    out[paving & ~has_i & ~lit] = UKENDT_TYPE
    if p["median_filter_px"] > 1:
        out = np.where(paving, median_filter(out, size=p["median_filter_px"]), 0).astype("uint8")
        out[paving & (out == 0)] = UKENDT_TYPE
    return out


def areas(types: np.ndarray, res: float) -> dict:
    return {TYPE_NAMES[t]: round(float((types == t).sum()) * res * res, 1) for t in TYPE_NAMES}


def png(path, types: np.ndarray) -> None:
    rgba = np.zeros((*types.shape, 4), dtype="uint8")
    for t, color in TYPE_COLORS.items():
        rgba[types == t] = (*color, 255)
    Image.fromarray(rgba).save(path, optimize=True)


def legend() -> dict:
    return {"titel": "Belægningstype (udkast)",
            "kategorier": [{"navn": TYPE_NAMES[t], "farve": "#%02x%02x%02x" % TYPE_COLORS[t]} for t in TYPE_NAMES],
            "note": "Udkast, ikke tjekket mod facit.",
            "info": ("Regelbaseret inden for klassen belægning. Punktskyens intensitet (laserens refleksion fra "
                     "terrænpunkter) skelner mørk asfalt fra lys sten og grus og virker også i skygge. "
                     "Ortofotoets farve finder grus (varm farve, lidt grønt). Fliser, belægningssten og beton "
                     "kan ikke skelnes i 0,25 m.")}
