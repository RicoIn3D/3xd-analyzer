"""3D model of the property for Unity and the browser: terrain and buildings as binary glTF (.glb).

Buildings come in two variants:
- simple: footprint walls with a parametric roof (flat, gable, hip, mono-pitch or barrel) built from the roof
  shape, pitch and heights that buildings.py estimates from the DHM surface model. A clean baseline.
- measured: the roof surface taken straight from the DHM surface model, only where the point cloud sees the roof.
  Coloured by its deviation from the simple model, so the baseline can be checked.

Trees also come in two variants:
- simple: trunk and ellipsoid crown per tree, at GeoDanmark tree points and at crown tops the surface model shows
  inside the tree class. Height and crown size are measured in the canopy height model (surface minus terrain).
- measured: the point cloud's vegetation points (ASPRS 4 and 5) on the property as glTF points.

Units are metres. The origin is the property centroid at median ground level (written to model.json).
glTF is Y-up and right-handed: x = east, y = up, z = south.
"""

import base64
import io
import json
import math
import os
import struct
from pathlib import Path

import numpy as np
import rasterio
import requests
import shapely
from PIL import Image
from rasterio.enums import Resampling
from rasterio.features import rasterize
from scipy import ndimage
from rasterio.windows import Window
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.geometry.polygon import orient

from analyzer import VERSION
from analyzer.buildings import NO_BBR_COLOR, TAG_COLORS, public
from analyzer.classes import BYGNING, COLORS, TRAEER, UDENFOR
from analyzer.grid import Grid
from analyzer.paving import TYPE_COLORS
from analyzer.sources.skraafoto import STAC_URL, project

DEFAULTS = {
    "terraen_skridt_px": 2,       # terrain mesh uses every n-th grid cell (0.5 m on the 0.25 m grid)
    "min_tagfod_m": 2.0,          # eaves are never lower than this above ground
    "min_tagfod_maalt_m": 1.0,    # same floor for eaves fitted to the surface model: the ground is the median around
                                  # the building, so on a slope the downhill eaves are measured lower than they stand
    "etagehoejde_m": 3.0,         # height per BBR storey when the surface model has no height for a building
    "tondetag_strimler": 6,       # strips per roof side approximating a barrel roof
    "afvigelse_skala_m": 1.0,     # deviation shown as full red (roof higher) or blue (lower) in the measured roof
    "tilpasning_min_celler": 20,  # roof heights are fitted to the surface model when this many roof cells are seen
    "geodanmark_max_noejagtighed_m": 0.5,  # GeoDanmark roof-edge height is used when measured at least this precisely
    "geodanmark_min_tagkant_m": 2.0,  # and when it is at least this high (lower is a measuring error, not a roof)
    "standard_tagkant_m": 2.4,    # roof edge above the highest ground under an unmeasured building with no BBR storeys
    "aaben_tagtykkelse_m": 0.2,   # open buildings (carport, overdækning): roof slab thickness
    "aaben_stolpe_m": 0.15,       # post width
    "aaben_stolpe_afstand_m": 3.0,  # largest distance between posts along the outline
    "gennemgang_min_vaeg_m": 3.0,  # buildings sharing a wall at least this long are checked for a covered passage
    "gennemgang_baand_m": 6.0,    # how far from the shared wall the passage is looked for
    "gennemgang_over_tag_m": 0.8,  # passage roof: surface model at least this far above the building's fitted roof
    "gennemgang_min_andel": 0.5,  # share of the band's roof cells that must be passage roof
    "gennemgang_min_bredde_m": 1.5,  # narrower bands are a ridge or a chimney, not a passage
    "terraen_tekstur_m": 0.1,     # terrain texture resolution: the orthophoto is fetched separately at this size
    "tilpasning_afviger_m": 0.5,
    "udhaeng_max_m": 0.8,         # roof overhang (outline at the roof edge, walls set back): larger is not believed
    "udhaeng_min_m": 0.15,        # smaller is rounded to no overhang
    "udhaeng_standard_m": 0.4,    # fallback for buildings that are not small (BBR 9xx) when BBR cannot be used
    "udhaeng_nabo_m": 0.3,        # outline edges this close to another building are shared walls and stay put
    "udhaeng_max_tab": 0.4,       # wall outline may lose at most this share of the outline's area
    "udhaeng_stern_m": 0.2,       # roof thickness at the edge (fascia) and under the overhang (soffit)
    "floej_max_fyldning": 0.9,    # a gable or hip footprint filling less of its rectangle is split into wings
    "floej_min_areal_m2": 6.0,    # smaller pieces (bays, porches) are not wings: they extend a wing or sit at eaves
    "floej_min_bredde_m": 2.0,
    "floej_min_daekning": 0.85,   # the wings must cover this share of the footprint, else one roof is kept
    "floej_snap_m": 0.3,          # outline corners closer than this along an axis share one cut line
    "skraafoto_min_vinkel": 0.25,  # an oblique textures a triangle only when it faces the camera at least this much
                                   # (cosine between the triangle normal and the direction to the camera)
    "skraafoto_max_side_px": 3000,  # texture size limit per oblique
    "skraafoto_margin_px": 60,  # cells further than this from the fit (chimneys, trees) are dropped and refitted
    "vaeg_fod_radius_m": 1.0,     # a wall's foot follows the lowest terrain within this distance of the wall point
    "vaeg_fod_under_m": 0.1,      # and goes this far below it, so no gap shows between wall and terrain mesh
    "vaeg_segment_m": 1.0,        # walls are split into pieces at most this long, so the foot can follow the terrain
    "trae_min_hoejde_m": 3.0,     # a crown top in the surface model counts as a tree from this height
    "trae_top_radius_m": 2.5,     # crown tops closer than this are one tree; also how far a GeoDanmark point looks
    "trae_max_krone_radius_m": 8.0,
    "trae_kronekant_andel": 0.5,  # the crown edge is where the canopy drops below this share of the tree height
    "trae_kronebund_andel": 0.35,  # crown base as share of the height (the surface model does not see under crowns)
    "trae_standard_hoejde_m": 5.0,  # GeoDanmark trees with no measurable height (felled, or young)
    "vegetation_max_punkter": 400_000,
}

# BBR byg032 wall material
WALL_COLORS = {
    "1": (165, 85, 60), "2": (210, 205, 195), "3": (225, 150, 60), "4": (205, 185, 150), "5": (150, 110, 75),
    "6": (185, 185, 180), "8": (125, 135, 145), "10": (195, 195, 185), "11": (232, 232, 232), "12": (150, 210, 230),
}
DEFAULT_WALL_COLOR = (215, 205, 190)

UP = np.array([0.0, 0.0, 1.0])

# BBR byg021 uses that are a roof on posts with open sides
OPEN_USES = {"920", "950"}  # carport, fritliggende overdækning


def _is_open(r: dict) -> bool:
    return bool(r.get("_open")) or r.get("_use_code") in OPEN_USES


class Mesh:
    """Triangles in absolute ENU coordinates (EPSG:25832 east, north, DVR90 height) with vertex colours."""

    def __init__(self):
        self.chunks = []  # (positions, normals, colours, indices) with indices local to the chunk
        self._tris = []

    def tri(self, a, b, c, color, facing=None) -> None:
        """Flat-shaded triangle, flipped so its normal points along `facing` when given."""
        a, b, c = (np.asarray(v, dtype="float64") for v in (a, b, c))
        n = np.cross(b - a, c - a)
        length = float(np.linalg.norm(n))
        if length < 1e-9:
            return
        if facing is not None and np.dot(n, facing) < 0:
            b, c, n = c, b, -n
        self._tris.append((a, b, c, n / length, color))

    def add(self, pos, nrm, col, idx) -> None:
        self.chunks.append((pos, nrm, col, idx))

    def arrays(self):
        chunks = list(self.chunks)
        if self._tris:
            pos = np.array([v for t in self._tris for v in t[:3]])
            nrm = np.repeat(np.array([t[3] for t in self._tris]), 3, axis=0)
            col = np.repeat(np.array([t[4] for t in self._tris], dtype="uint8"), 3, axis=0)
            chunks.append((pos, nrm, col, np.arange(len(pos))))
        if not chunks:
            return None
        offsets = np.cumsum([0] + [len(c[0]) for c in chunks[:-1]])
        return (np.vstack([c[0] for c in chunks]), np.vstack([c[1] for c in chunks]),
                np.vstack([c[2] for c in chunks]), np.concatenate([c[3] + o for c, o in zip(chunks, offsets)]))


# Roofs ---------------------------------------------------------------------------------------------------------

def _frame(geom):
    """Centre, long-axis and cross-axis unit vectors and half length/width of the minimum rotated rectangle."""
    pts = np.asarray(geom.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
    a = e1 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e2
    a = a / np.linalg.norm(a)
    b = np.array([-a[1], a[0]])
    c = pts.mean(axis=0)
    rel = pts - c
    return c, a, b, float(np.abs(rel @ a).max()), float(np.abs(rel @ b).max())


def _roof_kind(form: str | None) -> str:
    return {"fladt": "flat", "sadeltag": "gable", "valmtag": "hip", "pulttag (ensidigt)": "mono",
            "buet (tøndetag)": "barrel"}.get(form or "", "flat")


def _cell_centres(cells: np.ndarray, grid: Grid):
    rows, cols = np.nonzero(cells)
    return rows, cols, grid.xmin + (cols + 0.5) * grid.res, grid.ymax - (rows + 0.5) * grid.res


def fit_roofs(records: list[dict], grid: Grid, dsm: np.ndarray | None, p: dict = DEFAULTS) -> None:
    """Choose a parametric roof per building and record its heights and its deviation from the surface model."""
    for r in records:
        r["_roof"] = None
        ground = r["_ground_z"]
        if ground is None:
            continue
        c, a, b, half_l, half_w = _frame(r["_geom"])
        kind = _roof_kind(r["tagform"])
        label = r["tagform"] if kind != "flat" or r["tagform"] == "fladt" else "fladt (forenklet)"
        h_med, h_max = r["hoejde_median_m"], r["hoejde_max_m"]
        # An uncertain roof (hidden by trees, or built after the scan) gets no height from the surface model:
        # it would measure the tree crowns or whatever stood there before
        uncertain = (r["tagform"] or "").startswith("usikker")
        if uncertain:
            h_med = h_max = None
        if h_med is None:
            # No surface-model height (built after the scan, or hidden): GeoDanmark's measured roof edge, else BBR
            accuracy = r["geodanmark_hoejde_noejagtighed_m"]
            edge = r["geodanmark_tagkant_m"]
            if (edge is not None and edge >= p["geodanmark_min_tagkant_m"] and accuracy is not None
                    and float(accuracy) <= p["geodanmark_max_noejagtighed_m"]
                    and r["geodanmark_hoejde_metode"] != "Manuel"):
                h_med = h_max = edge
                kind, label = "flat", "fladt i tagkant (højde fra GeoDanmark)"
            else:
                # Measured from the highest ground under the building: on a slope the median ground around it
                # left the roof low on the uphill side and several metres up on the downhill side
                if r.get("_top_ground_z") is not None:
                    ground = r["_top_ground_z"]
                if str(r["etager"] or "").isdigit() and int(r["etager"]) > 0:
                    h_med = h_max = int(r["etager"]) * p["etagehoejde_m"]
                    kind, label = "flat", "fladt (højde fra BBR-etager)"
                else:
                    h_med = h_max = p["standard_tagkant_m"]
                    kind, label = "flat", "fladt (standardhøjde)"
        pitch = r["taghaeldning_grader"]
        if kind != "flat" and pitch is None:
            kind, label = "flat", "fladt (forenklet)"
        roof = {"kind": kind, "c": c, "a": a, "b": b, "L": half_l, "W": half_w, "ground": ground, "side": 1.0}
        if kind == "flat":
            roof["eaves"] = roof["ridge"] = max(h_med, p["min_tagfod_m"])
        else:
            run = 2 * half_w if kind == "mono" else half_w
            ridge = max(h_max, p["min_tagfod_m"])
            roof["ridge"] = ridge
            roof["eaves"] = max(ridge - run * math.tan(math.radians(pitch)), p["min_tagfod_m"])
        if kind == "mono" and dsm is not None and r["_roof_cells"] is not None:
            rows, cols, x, y = _cell_centres(r["_roof_cells"], grid)
            v = (x - c[0]) * b[0] + (y - c[1]) * b[1]
            z = dsm[rows, cols]
            hi, lo = z[v > 0], z[v < 0]
            if hi.size and lo.size and np.nanmean(hi) < np.nanmean(lo):
                roof["side"] = -1.0
        if (not uncertain and dsm is not None and r["_fit_cells"] is not None
                and r["_fit_cells"].sum() >= p["tilpasning_min_celler"]):
            # An L- or T-shaped house has one roof per wing; one roof over the whole rectangle puts the ridge
            # in the wrong place and the fit flattens it
            wings = _wings(r["_geom"], c, a, b, p) if kind in ("gable", "hip") else None
            parts = _fit_wings(roof, wings, r["_fit_cells"], grid, dsm, p) if wings else None
            if parts:
                roof.update({k: parts[0][k] for k in ("c", "a", "b", "L", "W", "eaves", "ridge")})
                roof["parts"] = parts
                wing_count = sum(1 for pt in parts if pt["kind"] != "flat")
                straight = sum(1 for pt in parts if pt.get("variant") == "lige fald")
                label = f"{label} ({wing_count} fløje" + (f", {straight} med lige fald fra kip" if straight else "") + ")"
            else:
                _fit_heights(roof, r["_fit_cells"], grid, dsm, p)
        r["_roof"] = roof
        r["model_tagform"] = label
        r["model_tagfod_m"] = round(roof["eaves"], 1)
        r["model_tagryg_m"] = round(roof["ridge"], 1)
        if dsm is not None and r["_roof_cells"] is not None and r["_roof_cells"].any():
            rows, cols, x, y = _cell_centres(r["_roof_cells"], grid)
            diff = dsm[rows, cols] - _roof_z(roof, x, y)
            diff = diff[~np.isnan(diff)]
            if diff.size:
                r["model_afvigelse_m"] = round(float(np.mean(np.abs(diff))), 2)


def _fit_heights(roof: dict, cells: np.ndarray, grid: Grid, dsm: np.ndarray, p: dict) -> None:
    """Least-squares eaves and ridge height for the roof's shape, refitted without outliers (chimneys, trees)."""
    rows, cols, x, y = _cell_centres(cells, grid)
    z = dsm[rows, cols] - roof["ground"]
    f = _roof_shape(roof, x, y)
    keep = ~np.isnan(z)
    for _ in range(3):
        if keep.sum() < p["tilpasning_min_celler"]:
            return
        if roof["kind"] == "flat" or np.ptp(f[keep]) < 0.2:
            eaves, rise = float(np.median(z[keep])), 0.0
        else:
            (eaves, rise), *_ = np.linalg.lstsq(np.column_stack([np.ones(keep.sum()), f[keep]]), z[keep], rcond=None)
        keep = ~np.isnan(z) & (np.abs(z - eaves - rise * f) < p["tilpasning_afviger_m"])
    eaves = max(float(eaves), p["min_tagfod_maalt_m"])
    roof["eaves"], roof["ridge"] = eaves, max(eaves + float(rise), eaves)


def _roof_shape(roof: dict, x, y):
    """Roof shape between 0 (eaves) and 1 (ridge) at world coordinates x, y."""
    c, a, b = roof["c"], roof["a"], roof["b"]
    half_l, half_w = roof["L"], roof["W"]
    u = np.clip((np.asarray(x) - c[0]) * a[0] + (np.asarray(y) - c[1]) * a[1], -half_l, half_l)
    v = np.clip((np.asarray(x) - c[0]) * b[0] + (np.asarray(y) - c[1]) * b[1], -half_w, half_w)
    kind = roof["kind"]
    if kind == "flat" or half_w < 1e-6:
        return np.zeros(np.shape(u))
    if kind == "gable":
        return 1 - np.abs(v) / half_w
    if kind == "hip":
        return np.minimum(half_w - np.abs(v), half_l - np.abs(u)) / half_w
    if kind == "mono":
        return (half_w + roof["side"] * v) / (2 * half_w)
    return 1 - (v / half_w) ** 2  # barrel, parabolic profile across the building


def _single_z(roof: dict, x, y):
    return roof["ground"] + roof["eaves"] + (roof["ridge"] - roof["eaves"]) * _roof_shape(roof, x, y)


def _local(roof: dict, x, y):
    """Coordinates along the roof's ridge (u) and across it (v)."""
    c, a, b = roof["c"], roof["a"], roof["b"]
    dx, dy = np.asarray(x, dtype=float) - c[0], np.asarray(y, dtype=float) - c[1]
    return dx * a[0] + dy * a[1], dx * b[0] + dy * b[1]


def _roof_z(roof: dict, x, y):
    """Absolute roof height at world coordinates x, y.

    A roof with wings is the highest wing roof over the point; that is what makes valleys where a wing meets the
    main roof. A point outside every wing (a small corner of the outline) takes the nearest wing's edge height.
    """
    parts = roof.get("parts")
    if not parts:
        return _single_z(roof, x, y)
    x, y = np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(np.asarray(y, dtype=float))
    z = np.vstack([_single_z(part, x, y) for part in parts])  # each wing's roof, clipped at its rectangle
    dist = []
    for part in parts:
        u, v = _local(part, x, y)
        dist.append(np.hypot(np.maximum(np.abs(u) - part["L"], 0), np.maximum(np.abs(v) - part["W"], 0)))
    dist = np.vstack(dist)
    inside = dist < 1e-3
    out = np.max(np.where(inside, z, -np.inf), axis=0)
    missing = ~inside.any(axis=0)
    if missing.any():
        nearest = np.argmin(dist[:, missing], axis=0)
        out[missing] = z[nearest, np.nonzero(missing)[0]]
    return out


def _wings(geom, c, a, b, p: dict):
    """Rectangles (u0, u1, v0, v1) in the building frame that make up an L-, T- or U-shaped footprint.

    Returns (wings, rest): wings largest first, rest the small pieces (bays, porches). None when the footprint is
    close enough to a rectangle for one roof, or not rectilinear enough to split.
    """
    if geom.geom_type != "Polygon" or geom.area <= 0:
        return None
    if geom.area / geom.minimum_rotated_rectangle.area >= p["floej_max_fyldning"]:
        return None
    local = affinity.affine_transform(geom, [a[0], a[1], b[0], b[1], -(c @ a), -(c @ b)])
    coords = np.asarray(local.exterior.coords)

    def cuts(values):
        groups = []
        for val in np.sort(values):
            if groups and val - groups[-1][-1] <= p["floej_snap_m"]:
                groups[-1].append(val)
            else:
                groups.append([val])
        return [float(np.mean(g)) for g in groups]

    us, vs = cuts(coords[:, 0]), cuts(coords[:, 1])
    if len(us) < 2 or len(vs) < 2 or len(us) * len(vs) > 900:
        return None
    filled = np.array([[local.intersection(box(us[i], vs[j], us[i + 1], vs[j + 1])).area
                        >= 0.5 * (us[i + 1] - us[i]) * (vs[j + 1] - vs[j])
                        for j in range(len(vs) - 1)] for i in range(len(us) - 1)])
    rects = []
    while filled.any():
        best, best_area = None, 0.0
        n_u, n_v = filled.shape
        for i0 in range(n_u):
            for i1 in range(i0, n_u):
                for j0 in range(n_v):
                    for j1 in range(j0, n_v):
                        if not filled[i0:i1 + 1, j0:j1 + 1].all():
                            break
                        area = (us[i1 + 1] - us[i0]) * (vs[j1 + 1] - vs[j0])
                        if area > best_area:
                            best, best_area = (i0, i1, j0, j1), area
        i0, i1, j0, j1 = best
        filled[i0:i1 + 1, j0:j1 + 1] = False
        rects.append((us[i0], us[i1 + 1], vs[j0], vs[j1 + 1]))
    big = [rc for rc in rects if (rc[1] - rc[0]) * (rc[3] - rc[2]) >= p["floej_min_areal_m2"]
           and min(rc[1] - rc[0], rc[3] - rc[2]) >= p["floej_min_bredde_m"]]
    rest = [rc for rc in rects if rc not in big]
    if len(big) < 2 or sum((rc[1] - rc[0]) * (rc[3] - rc[2]) for rc in big) < p["floej_min_daekning"] * geom.area:
        return None
    return big, rest


def _wing_roof(base: dict, c, a, b, rect, along_u: bool) -> dict:
    """Roof dict for one wing rectangle (building frame), ridge along u or along v."""
    u0, u1, v0, v1 = rect
    centre = c + (u0 + u1) / 2 * a + (v0 + v1) / 2 * b
    if along_u:
        ra, rb, half_l, half_w = a, b, (u1 - u0) / 2, (v1 - v0) / 2
    else:
        ra, rb, half_l, half_w = b, -a, (v1 - v0) / 2, (u1 - u0) / 2
    return {**{k: base[k] for k in ("kind", "ground", "eaves", "ridge")}, "side": 1.0,
            "c": centre, "a": ra, "b": rb, "L": half_l, "W": half_w}


def _shared_edge(rect, other, tol: float = 0.05):
    """Axis ('u' or 'v') and side (+1/-1) along which rect touches other over at least 1 m, else None."""
    u0, u1, v0, v1 = rect
    o_u0, o_u1, o_v0, o_v1 = other
    if min(u1, o_u1) - max(u0, o_u0) >= 1.0:
        if abs(v1 - o_v0) <= tol:
            return "v", 1
        if abs(v0 - o_v1) <= tol:
            return "v", -1
    if min(v1, o_v1) - max(v0, o_v0) >= 1.0:
        if abs(u1 - o_u0) <= tol:
            return "u", 1
        if abs(u0 - o_u1) <= tol:
            return "u", -1
    return None


def _fit_wings(roof: dict, wings, fit_cells: np.ndarray, grid: Grid, dsm: np.ndarray, p: dict) -> list[dict] | None:
    """Fit one roof per wing to the surface model. Returns the wing roofs, main wing first, or None.

    Each wing tries a ridge along and across its rectangle and keeps the one closer to the surface model. A wing
    whose ridge runs into a wing it touches is extended to that wing's ridge line, so the two roofs meet in valleys.
    """
    big, rest = wings
    c, a, b = roof["c"], roof["a"], roof["b"]
    rows, cols, x, y = _cell_centres(fit_cells, grid)
    u = (x - c[0]) * a[0] + (y - c[1]) * a[1]
    v = (x - c[0]) * b[0] + (y - c[1]) * b[1]
    parts, cores = [], []
    for k, rect in enumerate(big):
        u0, u1, v0, v1 = rect
        core = (u >= u0) & (u <= u1) & (v >= v0) & (v <= v1)
        if core.sum() < p["tilpasning_min_celler"]:
            if k == 0:
                return None
            continue
        mask = np.zeros(fit_cells.shape, dtype=bool)
        mask[rows[core], cols[core]] = True
        touch = next(((j, e) for j, other in enumerate(cores) if (e := _shared_edge(rect, other))), None)
        candidates = []
        for along_u in (True, False):
            ext = list(rect)
            if touch is not None:
                j, (axis, side) = touch
                o_u0, o_u1, o_v0, o_v1 = cores[j]
                # Ridge across the shared edge: run it into the other wing up to that wing's centre line
                if axis == "v" and not along_u:
                    ext[3 if side > 0 else 2] = (o_v0 + o_v1) / 2
                elif axis == "u" and along_u:
                    ext[1 if side > 0 else 0] = (o_u0 + o_u1) / 2
            candidates.append(_wing_roof(roof, c, a, b, ext, along_u))
        if touch is not None:
            # The neighbour's roof slope running straight on over the wing: one plane, high side at the shared edge
            j, (axis, side) = touch
            mono = _wing_roof(roof, c, a, b, rect, axis == "v")
            mono["kind"] = "mono"
            mono["side"] = float(side) if axis == "v" else -float(side)
            mono["variant"] = "lige fald"
            candidates.append(mono)
        best = None
        for part in candidates:
            _fit_heights(part, mask, grid, dsm, p)
            diff = dsm[rows[core], cols[core]] - _single_z(part, x[core], y[core])
            err = float(np.nanmean(np.abs(diff)))
            if best is None or err < best[0]:
                best = (err, part)
        parts.append(best[1])
        cores.append(rect)
    if len(parts) < 2:
        return None
    for rect in rest:
        _attach(rect, parts, cores, c, a, b)
    return parts


def _attach(rect, parts: list[dict], cores: list, c, a, b) -> None:
    """A small piece (bay, porch) at a wing's gable end extends that wing; elsewhere it gets a flat roof at eaves."""
    for part, core in zip(parts, cores):
        edge = _shared_edge(rect, core)
        if edge is None:
            continue
        axis, _ = edge
        along_u = abs(float(part["a"] @ a)) > 0.5
        corners = np.array([c + uu * a + vv * b for uu in rect[:2] for vv in rect[2:]])
        pu, pv = _local(part, corners[:, 0], corners[:, 1])
        if (axis == "u") == along_u and pv.min() >= -part["W"] - 0.05 and pv.max() <= part["W"] + 0.05:
            lo, hi = min(pu.min(), -part["L"]), max(pu.max(), part["L"])
            part["c"] = part["c"] + (lo + hi) / 2 * part["a"]
            part["L"] = (hi - lo) / 2
        else:
            flat = _wing_roof(part, c, a, b, rect, True)
            flat.update(kind="flat", ridge=part["eaves"], eaves=part["eaves"])
            parts.append(flat)
            cores.append(rect)
        return


def _plane(part: dict, poly: Polygon, origin) -> np.ndarray:
    """Plane z = p0 + p1*(x-ox) + p2*(y-oy) through the part's roof over the polygon."""
    xy = np.vstack([np.asarray(poly.exterior.coords), np.asarray(poly.representative_point().coords)])
    z = _single_z(part, xy[:, 0], xy[:, 1])
    m = np.column_stack([np.ones(len(xy)), xy[:, 0] - origin[0], xy[:, 1] - origin[1]])
    return np.linalg.lstsq(m, z, rcond=None)[0]


def _halfplane(poly: Polygon, coef: np.ndarray, origin) -> Polygon:
    """The part of poly's bounding box where coef[0] + coef[1]*(x-ox) + coef[2]*(y-oy) >= 0."""
    x0, y0, x1, y1 = poly.bounds
    ring = [(x0 - 1, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y1 + 1), (x0 - 1, y1 + 1)]
    f = [coef[0] + coef[1] * (px - origin[0]) + coef[2] * (py - origin[1]) for px, py in ring]
    out = []
    for i in range(4):
        (pa, fa), (pb, fb) = (ring[i], f[i]), (ring[(i + 1) % 4], f[(i + 1) % 4])
        if fa >= 0:
            out.append(pa)
        if (fa >= 0) != (fb >= 0):
            t = fa / (fa - fb)
            out.append((pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1])))
    return Polygon(out) if len(out) >= 3 else Polygon()


def _composite_regions(roof: dict, strips: int) -> list[Polygon]:
    """Planar pieces of a roof with wings: overlay the wings' planar pieces, then split each overlap where one
    wing's plane rises above the other's (the valley line). The rest of the plane is left to the nearest wing."""
    origin = roof["c"]
    cells: list[tuple[Polygon, list[np.ndarray]]] = []
    for part in roof["parts"]:
        regs = [(g, _plane(part, g, origin)) for g in _regions(part, strips)]
        nxt = []
        for poly, planes in cells:
            rest = poly
            for g, pl in regs:
                nxt += [(q, planes + [pl]) for q in _polygons(poly.intersection(g))]
                rest = rest.difference(g)
            nxt += [(q, planes) for q in _polygons(rest)]
        covered = shapely.union_all([poly for poly, _ in cells]) if cells else Polygon()
        for g, pl in regs:
            nxt += [(q, [pl]) for q in _polygons(g.difference(covered))]
        cells = nxt
    out = []
    for poly, planes in cells:
        if len(planes) == 1:
            out.append(poly)
            continue
        taken = Polygon()
        for i, pi in enumerate(planes):
            piece = poly
            for j, pj in enumerate(planes):
                if j != i:
                    piece = piece.intersection(_halfplane(poly, pi - pj, origin))
            piece = piece.difference(taken)
            out += _polygons(piece)
            taken = taken.union(piece)
    union = shapely.union_all(out)
    x0, y0, x1, y1 = union.bounds
    out += _polygons(box(x0 - 50, y0 - 50, x1 + 50, y1 + 50).difference(union))
    return [g.buffer(1e-4, join_style=2) for g in out]


def _regions(roof: dict, strips: int) -> list[Polygon]:
    """Parts of the roof's rectangle in which the roof is planar (or nearly, for a barrel), in world coordinates."""
    if roof.get("parts"):
        return _composite_regions(roof, strips)
    half_l, half_w = roof["L"], roof["W"]
    kind = roof["kind"]
    if kind == "gable":
        local = [box(-half_l, 0, half_l, half_w), box(-half_l, -half_w, half_l, 0)]
    elif kind == "hip":
        k = max(half_l - half_w, 0.0)
        local = [Polygon([(-half_l, half_w), (half_l, half_w), (k, 0), (-k, 0)]),
                 Polygon([(-half_l, -half_w), (half_l, -half_w), (k, 0), (-k, 0)]),
                 Polygon([(half_l, half_w), (half_l, -half_w), (k, 0)]),
                 Polygon([(-half_l, half_w), (-half_l, -half_w), (-k, 0)])]
    elif kind == "barrel":
        edges = np.linspace(-half_w, half_w, 2 * strips + 1)
        local = [box(-half_l, v0, half_l, v1) for v0, v1 in zip(edges[:-1], edges[1:])]
    else:
        local = [box(-half_l, -half_w, half_l, half_w)]
    c, a, b = roof["c"], roof["a"], roof["b"]
    # Grow slightly so the pieces cover the footprint without float gaps along shared edges
    return [affinity.affine_transform(poly, [a[0], b[0], a[1], b[1], c[0], c[1]]).buffer(1e-4,
                                                                                                     join_style=2)
            for poly in local if poly.area > 1e-9]


def _polygons(geom):
    return [g for g in shapely.get_parts(geom) if isinstance(g, Polygon) and g.area > 1e-6]


def _densify(ring: np.ndarray, extra: np.ndarray) -> np.ndarray:
    """Insert the points from `extra` that lie on the ring's segments, so walls follow roof creases."""
    out = []
    for p0, p1 in zip(ring[:-1], ring[1:]):
        out.append(p0)
        d = p1 - p0
        seg2 = float(d @ d)
        if seg2 < 1e-12:
            continue
        rel = extra - p0
        t = rel @ d / seg2
        dist = np.abs(rel[:, 0] * d[1] - rel[:, 1] * d[0]) / math.sqrt(seg2)
        on = (dist < 1e-3) & (t > 1e-6) & (t < 1 - 1e-6)
        for tt in np.unique(np.round(t[on], 6)):
            out.append(p0 + tt * d)
    out.append(ring[-1])
    return np.array(out)


def _subdivide(ring: np.ndarray, max_len: float) -> np.ndarray:
    """Split ring segments longer than max_len into equal pieces."""
    out = []
    for p0, p1 in zip(ring[:-1], ring[1:]):
        n = max(1, math.ceil(float(np.linalg.norm(p1 - p0)) / max_len))
        out.extend(p0 + (p1 - p0) * (k / n) for k in range(n))
    out.append(ring[-1])
    return np.array(out)


def _wall_foot(dtm: np.ndarray, grid: Grid, p: dict):
    """Height of a wall's foot at (x, y): just below the lowest terrain near the point, NaN outside the grid.

    The terrain mesh is drawn under buildings too (the DHM is interpolated there), so the foot follows the
    terrain along the wall instead of going down to the building's lowest point. On a slope that lowest point
    is metres under the ground on the uphill side, where the wall then showed below the terrain.
    """
    k = 2 * max(1, round(p["vaeg_fod_radius_m"] / grid.res)) + 1
    low = ndimage.minimum_filter(np.where(np.isnan(dtm), np.inf, dtm), size=k, mode="nearest")
    low = np.where(np.isinf(low), np.nan, low) - p["vaeg_fod_under_m"]

    def foot(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        col = np.floor((x - grid.xmin) / grid.res).astype(int)
        row = np.floor((grid.ymax - y) / grid.res).astype(int)
        ok = (row >= 0) & (row < low.shape[0]) & (col >= 0) & (col < low.shape[1])
        z = np.full(len(x), np.nan)
        z[ok] = low[row[ok], col[ok]]
        return z

    return foot


def _free_edges(geom, others: list, p: dict):
    """Outline edges not shared with another building (or passage): only these get an overhang."""
    near = [o.buffer(p["udhaeng_nabo_m"]) for o in others if o is not None and o.distance(geom) < p["udhaeng_nabo_m"]]
    return geom.boundary.difference(shapely.union_all(near)) if near else geom.boundary


def _wall_outline(geom, d: float, free, p: dict):
    """The outline moved in by d along its free edges, or None when that breaks the building apart."""
    if d <= 0 or free.is_empty:
        return geom
    if free.equals(geom.boundary):
        wall = geom.buffer(-d, join_style=2, mitre_limit=2.0)
    else:
        wall = geom.difference(free.buffer(d, cap_style=2, join_style=2, mitre_limit=2.0))
    wall = shapely.union_all(_polygons(wall)) if not wall.is_empty else wall
    if (wall.is_empty or len(_polygons(wall)) != len(_polygons(geom))
            or wall.area < (1 - p["udhaeng_max_tab"]) * geom.area):
        return None
    return wall


def _overhang_from_bbr(geom, bbr_area, free, p: dict) -> float | None:
    """Overhang d at which the wall outline has BBR's built-up area (measured at the outer walls)."""
    try:
        target = float(bbr_area)
    except (TypeError, ValueError):
        return None
    if target <= 0 or target >= geom.area or free.is_empty:
        return None

    def area(d):
        wall = _wall_outline(geom, d, free, p)
        return None if wall is None else wall.area

    high = area(p["udhaeng_max_m"])
    if high is None or high > target:
        return None  # needs more than the cap: BBR leaves something out (open roofed area, other storey)
    lo, hi = 0.0, p["udhaeng_max_m"]
    for _ in range(30):
        mid = (lo + hi) / 2
        a = area(mid)
        if a is None:
            return None
        lo, hi = (mid, hi) if a > target else (lo, mid)
    return (lo + hi) / 2


def estimate_overhang(records: list[dict], p: dict = DEFAULTS) -> None:
    """Roof overhang per building: GeoDanmark draws the outline at the roof edge, BBR's area at the outer walls.

    The overhang is the inward offset of the free edges (not walls shared with a neighbour) at which the outline
    has BBR's built-up area. Sets model_udhaeng_m, model_udhaeng_kilde and r["_overhang"]; the walls themselves
    are placed in write(), after passages are cut out.
    """
    for r in records:
        r["_overhang"] = 0.0
        geom = r["_geom"]
        if r.get("_roof") is None or geom.geom_type != "Polygon":
            continue
        small = str(r.get("_use_code") or "").startswith("9")
        if _is_open(r):
            d, source = 0.0, "ingen (åben)"
        elif r.get("_maalested") in ("Væg", "Tag og Væg"):
            d, source = 0.0, "ingen (omrids målt ved væg)"
        else:
            others = [o["_geom"] for o in records if o is not r]
            free = _free_edges(geom, others, p)
            d = _overhang_from_bbr(geom, r["bebygget_areal_bbr_m2"], free, p)
            if d is not None:
                source = "BBR-areal"
            else:
                d = 0.0 if small else p["udhaeng_standard_m"]
                source = "standard (småbygning)" if small else "standard"
                if d and _wall_outline(geom, d, free, p) is None:
                    d, source = 0.0, "ingen (omridset for smalt)"
        if 0 < d < p["udhaeng_min_m"]:
            d = 0.0
        r["_overhang"] = d
        r["model_udhaeng_m"] = round(d, 2)
        r["model_udhaeng_kilde"] = source


def place_walls(models: list[dict], p: dict = DEFAULTS) -> None:
    """Wall outline per building from its overhang, on the 3D outline (passages cut out). r["_wall_geom"]."""
    outlines = [m.get("_geom_3d", m["_geom"]) for m in models]
    for r, geom in zip(models, outlines):
        r["_wall_geom"] = None
        d = r.get("_overhang") or 0.0
        if d <= 0 or _is_open(r):
            continue
        free = _free_edges(geom, [o for o in outlines if o is not geom], p)
        wall = _wall_outline(geom, d, free, p)
        if wall is not None and wall is not geom:
            r["_wall_geom"] = wall


def _building_mesh(r: dict, mesh: Mesh, p: dict, foot=None) -> None:
    """Simple building: roof from the fitted shape, walls from the outline down to the terrain.

    foot (from _wall_foot) gives the wall's foot along the outline; without it, or where it has no value,
    walls go down to the building's lowest terrain point.
    """
    roof = r["_roof"]
    footprints = [orient(g, 1.0) for g in _polygons(r.get("_geom_3d", r["_geom"]))]
    roof_color = r.get("_roof_color") or (TAG_COLORS.get(str(r["tagmateriale_kode"]), NO_BBR_COLOR)
                                          if r["bbr_fundet"] else NO_BBR_COLOR)
    wall_color = WALL_COLORS.get(str(r["_wall_code"]), DEFAULT_WALL_COLOR)
    lowest = r["_base_z"] if r["_base_z"] is not None else roof["ground"]
    pieces = [pc for geom in footprints for region in _regions(roof, p["tondetag_strimler"])
              for pc in _polygons(geom.intersection(region))]
    is_open = _is_open(r)
    slab = p["aaben_tagtykkelse_m"] if is_open else 0.0
    corners = []
    for piece in pieces:
        for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(piece)):
            xy = np.asarray(tri.exterior.coords)[:3]
            z = _roof_z(roof, xy[:, 0], xy[:, 1])
            mesh.tri(*np.column_stack([xy, z]), roof_color, facing=UP)
            if is_open:  # underside, seen from under the open roof
                mesh.tri(*np.column_stack([xy, z - slab]), roof_color, facing=-UP)
        corners += [np.asarray(ring.coords) for ring in (piece.exterior, *piece.interiors)]
    extra = np.vstack(corners) if corners else np.empty((0, 2))
    walls = r.get("_wall_geom")
    if walls is not None and not is_open:
        _overhang_mesh(r, mesh, p, foot, footprints, walls, extra, roof_color, wall_color, lowest)
        return
    for ring in (ring for geom in footprints for ring in (geom.exterior, *geom.interiors)):
        pts = _densify(np.asarray(ring.coords), extra)
        if foot is not None and not is_open:
            pts = _subdivide(pts, p["vaeg_segment_m"])
        top = _roof_z(roof, pts[:, 0], pts[:, 1])
        if is_open:  # only the roof slab's edge; the sides are open
            _wall_band(mesh, pts, top - slab, top, roof_color)
            continue
        base = np.full(len(pts), lowest) if foot is None else foot(pts[:, 0], pts[:, 1])
        base = np.minimum(np.where(np.isnan(base), lowest, base), top)
        _wall_band(mesh, pts, base, top, wall_color)
    if is_open:
        for geom in footprints:
            for x, y in _post_positions(geom, p):
                z0 = foot(np.array([x]), np.array([y]))[0] if foot is not None else np.nan
                z0 = lowest if np.isnan(z0) else z0
                z1 = float(_roof_z(roof, np.array([x]), np.array([y]))[0]) - slab
                _post(mesh, x, y, min(z0, z1), z1, p["aaben_stolpe_m"], wall_color)


def _overhang_mesh(r: dict, mesh: Mesh, p: dict, foot, footprints, walls, roof_corners, roof_color, wall_color,
                   lowest: float) -> None:
    """Fascia at the roof edge, soffit under the overhang, walls at the wall outline up to the soffit."""
    roof = r["_roof"]
    t = p["udhaeng_stern_m"]
    regions = _regions(roof, p["tondetag_strimler"])
    for ring in (ring for geom in footprints for ring in (geom.exterior, *geom.interiors)):
        pts = _densify(np.asarray(ring.coords), roof_corners)
        top = _roof_z(roof, pts[:, 0], pts[:, 1])
        _wall_band(mesh, pts, top - t, top, roof_color)
    corners = []
    for geom in footprints:
        for region in regions:
            for piece in _polygons(geom.difference(walls).intersection(region)):
                for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(piece)):
                    xy = np.asarray(tri.exterior.coords)[:3]
                    z = _roof_z(roof, xy[:, 0], xy[:, 1]) - t
                    mesh.tri(*np.column_stack([xy, z]), roof_color, facing=-UP)
            corners += [np.asarray(ring.coords) for pc in _polygons(walls.intersection(region))
                        for ring in (pc.exterior, *pc.interiors)]
    extra = np.vstack(corners) if corners else np.empty((0, 2))
    for wall in (orient(g, 1.0) for g in _polygons(walls)):
        for ring in (wall.exterior, *wall.interiors):
            pts = _densify(np.asarray(ring.coords), extra)
            if foot is not None:
                pts = _subdivide(pts, p["vaeg_segment_m"])
            top = _roof_z(roof, pts[:, 0], pts[:, 1]) - t
            base = np.full(len(pts), lowest) if foot is None else foot(pts[:, 0], pts[:, 1])
            base = np.minimum(np.where(np.isnan(base), lowest, base), top)
            _wall_band(mesh, pts, base, top, wall_color)


def _wall_band(mesh: Mesh, pts: np.ndarray, base: np.ndarray, top: np.ndarray, color) -> None:
    """Vertical faces along a ring from base to top (both per point)."""
    for i in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        outward = np.array([y1 - y0, x0 - x1, 0.0])  # exterior is counter-clockwise, holes clockwise
        q = [(x0, y0, base[i]), (x1, y1, base[i + 1]), (x1, y1, top[i + 1]), (x0, y0, top[i])]
        mesh.tri(q[0], q[1], q[2], color, facing=outward)
        mesh.tri(q[0], q[2], q[3], color, facing=outward)


def _post_positions(geom: Polygon, p: dict) -> list[tuple[float, float]]:
    """Posts at the corners of an open building and along its sides, set in from the outline by a post width."""
    inner = geom.buffer(-p["aaben_stolpe_m"], join_style="mitre")
    ring = inner.exterior if isinstance(inner, Polygon) and not inner.is_empty else geom.exterior
    pts = _subdivide(np.asarray(shapely.simplify(ring, 0.05).coords), p["aaben_stolpe_afstand_m"])
    return [tuple(pt) for pt in pts[:-1]]


def _post(mesh: Mesh, x: float, y: float, z0: float, z1: float, width: float, color) -> None:
    h = width / 2
    square = np.array([(x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h), (x - h, y - h)])
    _wall_band(mesh, square, np.full(5, z0), np.full(5, z1), color)


# Covered passages ---------------------------------------------------------------------------------------------

PASSAGE_COLOR = (225, 228, 230)


def find_passages(records: list[dict], grid: Grid, dsm: np.ndarray, p: dict = DEFAULTS) -> list[dict]:
    """Covered passages between buildings that share a wall, found in the surface model. Returns model records.

    GeoDanmark draws such a passage (a roofed opening through a row of houses) as part of the buildings on each
    side. Its roof stands clear of the buildings' fitted roof, so it shows as a band along the shared wall where
    the surface model is well above that roof. The band is cut out of both buildings (in the 3D model only:
    r["_geom_3d"]) and returned as an open building with a barrel roof fitted to the surface model.
    """
    found = []
    usable = [r for r in records if r.get("_roof") is not None and r.get("_roof_cells") is not None]
    for i, ri in enumerate(usable):
        for rj in usable[i + 1:]:
            gi, gj = ri.get("_geom_3d", ri["_geom"]), rj.get("_geom_3d", rj["_geom"])
            if gi.distance(gj) > 0.05:
                continue
            shared = [g for g in shapely.get_parts(gi.boundary.buffer(0.05).intersection(gj.boundary))
                      if isinstance(g, LineString)]
            line = _straight(shapely.line_merge(MultiLineString(shared))) if shared else None
            if line is None or line.length < p["gennemgang_min_vaeg_m"]:
                continue
            sides = []
            for r, g in ((ri, gi), (rj, gj)):
                band = g.intersection(_band(line, p["gennemgang_baand_m"]))
                rows, cols, x, y = _cell_centres(r["_roof_cells"], grid)
                inside = shapely.contains_xy(band, x, y)
                if inside.sum() < p["tilpasning_min_celler"]:
                    sides.append(None)
                    continue
                rows, cols, x, y = rows[inside], cols[inside], x[inside], y[inside]
                above = dsm[rows, cols] - _roof_z(r["_roof"], x, y) > p["gennemgang_over_tag_m"]
                dist = shapely.distance(line, shapely.points(x, y))
                if not above.any():
                    sides.append(None)
                    continue
                reach = float(np.percentile(dist[above], 95))
                share = above[dist <= reach].mean()
                sides.append((g, reach) if share >= p["gennemgang_min_andel"] else None)
            if any(sd is None for sd in sides):
                continue
            parts = [g.intersection(_band(line, reach)) for g, reach in sides]
            geom = shapely.union_all(parts).buffer(0.05).buffer(-0.05)
            if geom.is_empty or min(reach for _, reach in sides) < p["gennemgang_min_bredde_m"] / 2:
                continue
            ri["_geom_3d"], rj["_geom_3d"] = gi.difference(geom), gj.difference(geom)
            found.append(_passage_record(len(found) + 1, geom, ri, grid, dsm, p))
    return found


def _straight(line) -> LineString | None:
    """The shared wall as one straight line between its outermost points.

    The shared boundary picks up a few centimetres of the facades at its ends (boundary.buffer(0.05)). A flat-capped
    buffer of that bent line ends square to the facade stub instead of to the wall, which cut a triangle out of the
    passage and left it standing in the passage as a sliver of the building.
    """
    if line is None or line.is_empty:
        return None
    pts = np.vstack([np.asarray(g.coords) for g in shapely.get_parts(line)])
    rel = pts - pts.mean(axis=0)
    t = rel @ np.linalg.svd(rel, full_matrices=False)[2][0]
    return LineString([pts[t.argmin()], pts[t.argmax()]])


def _band(line: LineString, dist: float) -> Polygon:
    """Band within dist of a straight line, with its ends pushed past the facades so it spans the building."""
    (x0, y0), (x1, y1) = line.coords
    d = np.array([x1 - x0, y1 - y0]) / line.length * dist
    longer = LineString([(x0 - d[0], y0 - d[1]), (x1 + d[0], y1 + d[1])])
    return longer.buffer(dist, cap_style="flat")


def _passage_record(n: int, geom, parent: dict, grid: Grid, dsm: np.ndarray, p: dict) -> dict:
    """Open building with a barrel roof running the same way as the parent building's roof."""
    pr = parent["_roof"]
    a, b = np.asarray(pr["a"]), np.asarray(pr["b"])
    pts = np.asarray(geom.minimum_rotated_rectangle.exterior.coords)[:4]
    c = pts.mean(axis=0)
    rel = pts - c
    roof = {"kind": "barrel", "c": c, "a": a, "b": b, "L": float(np.abs(rel @ a).max()),
            "W": float(np.abs(rel @ b).max()), "ground": pr["ground"], "side": 1.0,
            "eaves": pr["eaves"], "ridge": pr["ridge"]}
    cells = rasterize([(geom, 1)], out_shape=grid.shape, transform=grid.transform, fill=0,
                      dtype="uint8").astype(bool)
    _fit_heights(roof, cells, grid, dsm, p)
    extras = {"nr": f"G{n}", "anvendelse": "Overdækket gennemgang (fundet i højdemodellen)",
              "model_tagform": "buet, åben", "model_tagfod_m": round(roof["eaves"], 1),
              "model_tagryg_m": round(roof["ridge"], 1), "grundplan_m2": round(geom.area, 1)}
    return {"nr": f"G{n}", "_geom": geom, "_roof": roof, "_open": True, "_roof_color": PASSAGE_COLOR,
            "_wall_code": None, "_base_z": parent["_base_z"], "opfoerelsesaar": None, "_extras": extras}


def _terrain_texture(image: np.ndarray, grid: Grid, inside: np.ndarray, pos: np.ndarray):
    """Crop of a grid-aligned image (any resolution) over the terrain, and the terrain vertices' UVs in it."""
    rows, cols = np.nonzero(inside)
    k = image.shape[1] / grid.width
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    crop = image[int(r0 * k):int(np.ceil(r1 * k)), int(c0 * k):int(np.ceil(c1 * k))]
    x0, y1 = grid.xmin + c0 * grid.res, grid.ymax - r0 * grid.res
    w, h = (c1 - c0) * grid.res, (r1 - r0) * grid.res
    uv = np.column_stack([(pos[:, 0] - x0) / w, (y1 - pos[:, 1]) / h])
    if max(crop.shape[:2]) > 8192:  # largest texture most GPUs take
        f = 8192 / max(crop.shape[:2])
        crop = np.asarray(Image.fromarray(crop).resize((round(crop.shape[1] * f), round(crop.shape[0] * f)),
                                                       Image.LANCZOS))
    return np.ascontiguousarray(crop), uv.astype("float64")


# Height fields (terrain, measured roofs) -----------------------------------------------------------------------

def _heightfield(z: np.ndarray, mask: np.ndarray, colors: np.ndarray, grid: Grid, step: int = 1,
                 window: tuple[int, int, int, int] | None = None):
    """Indexed mesh through the centres of every `step`-th cell where mask is set. Smooth vertex normals."""
    r0, r1, c0, c1 = window or (0, z.shape[0], 0, z.shape[1])
    zs = z[r0:r1:step, c0:c1:step]
    ms = mask[r0:r1:step, c0:c1:step] & ~np.isnan(zs)
    cs = colors[r0:r1:step, c0:c1:step]
    quads = ms[:-1, :-1] & ms[1:, :-1] & ms[:-1, 1:] & ms[1:, 1:]
    if not quads.any():
        return None
    used = np.zeros_like(ms)
    for dr, dc in ((0, 0), (1, 0), (0, 1), (1, 1)):
        used[dr:dr + quads.shape[0], dc:dc + quads.shape[1]] |= quads
    ids = np.full(ms.shape, -1, dtype="int64")
    ids[used] = np.arange(int(used.sum()))
    rows, cols = np.nonzero(used)
    pos = np.column_stack([grid.xmin + (c0 + cols * step + 0.5) * grid.res,
                           grid.ymax - (r0 + rows * step + 0.5) * grid.res, zs[used]])
    i00, i10 = ids[:-1, :-1][quads], ids[1:, :-1][quads]
    i01, i11 = ids[:-1, 1:][quads], ids[1:, 1:][quads]
    # Rows run south, so (00, 10, 01) is counter-clockwise seen from above
    tris = np.concatenate([np.column_stack([i00, i10, i01]), np.column_stack([i01, i10, i11])])
    face = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    nrm = np.zeros_like(pos)
    for k in range(3):
        np.add.at(nrm, tris[:, k], face)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    return pos, nrm, cs[used].astype("uint8"), tris.ravel()


def _deviation_colors(diff: np.ndarray, scale: float) -> np.ndarray:
    """Grey at zero, red where the surface is above the simple model, blue where it is below."""
    t = np.clip(np.nan_to_num(diff) / scale, -1, 1)[..., None]
    grey, red, blue = np.array([200, 200, 200]), np.array([215, 48, 39]), np.array([49, 104, 190])
    return np.where(t >= 0, grey + (red - grey) * t, grey + (blue - grey) * -t).astype("uint8")


# Trees ---------------------------------------------------------------------------------------------------------

def find_trees(gd_trees: list[dict], grid: Grid, parcel, dtm: np.ndarray, dsm: np.ndarray | None,
               classes: np.ndarray, ortho: np.ndarray | None, p: dict = DEFAULTS) -> list[dict]:
    """Trees on the property with trunk position, height, crown radius, crown base and colour.

    GeoDanmark tree points come first (their position is the trunk). Crown tops in the canopy height model inside
    the tree class add the trees GeoDanmark is missing; tops within trae_top_radius_m of a GeoDanmark tree are
    that tree.
    """
    if dsm is None:
        return []
    res = grid.res
    chm = np.nan_to_num(dsm - dtm, nan=0.0)
    chm[classes == BYGNING] = 0.0
    smooth = ndimage.gaussian_filter(chm, 0.5 / res)
    near = int(round(p["trae_top_radius_m"] / res))
    peaks = (smooth == ndimage.maximum_filter(smooth, size=2 * near + 1)) & (smooth >= p["trae_min_hoejde_m"])
    peaks &= classes == TRAEER

    def cell(x, y):
        return int((grid.ymax - y) / res), int((x - grid.xmin) / res)

    def inside_grid(r, c):
        return 0 <= r < grid.height and 0 <= c < grid.width

    def disc(r, c, k):
        """Window bounds around (r, c) and a mask of the cells within k cells of it."""
        r0, r1, c0, c1 = max(r - k, 0), min(r + k + 1, grid.height), max(c - k, 0), min(c + k + 1, grid.width)
        rr, cc = np.ogrid[r0:r1, c0:c1]
        return (r0, r1, c0, c1), (rr - r) ** 2 + (cc - c) ** 2 <= k ** 2

    found = []
    for t in gd_trees:
        if t["objekttype"] != "Trae" or (parcel is not None and not parcel.contains(t["geom"])):
            continue
        r, c = cell(t["geom"].x, t["geom"].y)
        if not inside_grid(r, c):
            continue
        (r0, r1, c0, c1), m = disc(r, c, near)
        win = np.where(m, smooth[r0:r1, c0:c1], 0.0)
        tr, tc = np.unravel_index(int(np.argmax(win)), win.shape)
        found.append({"kilde": "GeoDanmark", "stamme": (r, c), "top": (r0 + tr, c0 + tc),
                      "hoejde": float(win[tr, tc])})
    for r, c in zip(*np.nonzero(peaks)):
        if any((r - f["top"][0]) ** 2 + (c - f["top"][1]) ** 2 <= near ** 2
               or (r - f["stamme"][0]) ** 2 + (c - f["stamme"][1]) ** 2 <= near ** 2 for f in found):
            continue
        found.append({"kilde": "Højdemodel", "stamme": (r, c), "top": (r, c), "hoejde": float(smooth[r, c])})

    trees = []
    for nr, f in enumerate(found, start=1):
        height, measured = f["hoejde"], f["hoejde"] >= p["trae_min_hoejde_m"]
        if not measured:
            height = p["trae_standard_hoejde_m"]
        (tr, tc), (sr, sc) = f["top"], f["stamme"]
        radius = _crown_radius(smooth, tr, tc, height, res, p) if measured else height / 4
        base = p["trae_kronebund_andel"] * height
        color = (70, 110, 55)
        if ortho is not None:
            (r0, r1, c0, c1), m = disc(tr, tc, max(1, int(radius * 0.7 / res)))
            sub = m & (smooth[r0:r1, c0:c1] > base)
            if sub.any():
                color = tuple(int(v) for v in np.median(ortho[r0:r1, c0:c1][sub], axis=0))
        trees.append({
            "nr": nr, "kilde": f["kilde"],
            "x": grid.xmin + (sc + 0.5) * res, "y": grid.ymax - (sr + 0.5) * res, "terraen_z": float(dtm[sr, sc]),
            "top_x": grid.xmin + (tc + 0.5) * res, "top_y": grid.ymax - (tr + 0.5) * res,
            "hoejde_m": round(height, 1), "hoejde_maalt": measured, "krone_diameter_m": round(2 * radius, 1),
            "kronebund_m": round(base, 1), "farve": color})
    return trees


def _crown_radius(chm: np.ndarray, r: int, c: int, height: float, res: float, p: dict) -> float:
    """Median distance, over 16 directions, until the canopy drops below the crown edge or rises into a neighbour."""
    edge = p["trae_kronekant_andel"] * height
    radii = []
    for a in np.linspace(0, 2 * np.pi, 16, endpoint=False):
        prev, dist = height, 0.0
        for step in range(1, int(p["trae_max_krone_radius_m"] / res) + 1):
            rr, cc = int(round(r - step * np.sin(a))), int(round(c + step * np.cos(a)))
            if not (0 <= rr < chm.shape[0] and 0 <= cc < chm.shape[1]):
                break
            v = chm[rr, cc]
            if v < edge or v > prev + 0.5:
                break
            prev, dist = min(prev, v), step * res
        radii.append(dist)
    return max(float(np.median(radii)), 1.0)


def _tree_mesh(t: dict, mesh: Mesh, lon: int = 14, lat: int = 9) -> None:
    """Ellipsoid crown centred over the crown top and a cylinder trunk from the ground into the crown."""
    g, h, base = t["terraen_z"], t["hoejde_m"], t["kronebund_m"]
    a, cz = t["krone_diameter_m"] / 2, g + (h + base) / 2
    b = (h - base) / 2
    phi = np.linspace(0, np.pi, lat + 1)[:, None]
    th = np.linspace(0, 2 * np.pi, lon, endpoint=False)[None, :]
    unit = np.stack([np.sin(phi) * np.cos(th), np.sin(phi) * np.sin(th), np.cos(phi) * np.ones_like(th)], -1)
    unit = unit.reshape(-1, 3)
    pos = unit * [a, a, b] + [t["top_x"], t["top_y"], cz]
    nrm = unit / [a, a, b]
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    idx = []
    for i in range(lat):
        for j in range(lon):
            p00, p01 = i * lon + j, i * lon + (j + 1) % lon
            p10, p11 = p00 + lon, p01 + lon
            idx += [p00, p10, p01, p01, p10, p11]
    mesh.add(pos, nrm, np.tile(np.array(t["farve"], dtype="uint8"), (len(pos), 1)), np.array(idx))

    sides, rad = 8, max(0.08, h / 60)
    top = g + base + 0.3 * (h - base)
    th = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    ring = np.column_stack([np.cos(th), np.sin(th), np.zeros(sides)])
    pos = np.vstack([ring * rad + [t["x"], t["y"], g - 0.2], ring * rad + [t["x"], t["y"], top]])
    idx = []
    for j in range(sides):
        k = (j + 1) % sides
        idx += [j, k, sides + j, sides + j, k, sides + k]
    mesh.add(pos, np.vstack([ring, ring]), np.tile(np.array((95, 75, 55), dtype="uint8"), (2 * sides, 1)),
             np.array(idx))


def _vegetation_points(veg: np.ndarray, parcel, grid: Grid, ortho: np.ndarray | None, p: dict):
    """Vegetation points on the property, thinned to vegetation_max_punkter, coloured from the orthophoto."""
    if parcel is not None:
        veg = veg[shapely.contains_xy(parcel, veg[:, 0], veg[:, 1])]
    if len(veg) > p["vegetation_max_punkter"]:
        veg = veg[np.random.default_rng(0).choice(len(veg), p["vegetation_max_punkter"], replace=False)]
    if not len(veg):
        return None
    if ortho is not None:
        rows = np.clip(((grid.ymax - veg[:, 1]) / grid.res).astype(int), 0, grid.height - 1)
        cols = np.clip(((veg[:, 0] - grid.xmin) / grid.res).astype(int), 0, grid.width - 1)
        colors = ortho[rows, cols]
    else:
        colors = np.where(veg[:, 3:4] == 5, [60, 110, 50], [120, 150, 70]).astype("uint8")
    return veg[:, :3], colors.astype("uint8")


# Obliques as textures -----------------------------------------------------------------------------------------

def fetch_obliques(images: list[dict], area, z_range: tuple[float, float], p: dict = DEFAULTS) -> list[dict]:
    """Clean crops of the run's oblique photos (no outline drawn) covering the area from ground to roof height.

    images are the run's skråfoto entries (id and dato). The STAC item gives the camera; the COG itself is public.
    """
    token = os.environ.get("DATAFORSYNINGEN_TOKEN", "").strip()
    x0, y0, x1, y1 = area.bounds
    corners = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in z_range]
    out = []
    for img in images:
        r = requests.get(f"{STAC_URL}/collections/skraafotos{img['dato'][:4]}/items/{img['id']}",
                         headers={"token": token}, timeout=60)
        if r.status_code != 200:
            continue
        item = r.json()
        props = item["properties"]
        px = np.array([project(props, *c) for c in corners])
        margin = p["skraafoto_margin_px"]
        c0, r0 = int(px[:, 0].min()) - margin, int(px[:, 1].min()) - margin
        c1, r1 = int(px[:, 0].max()) + margin, int(px[:, 1].max()) + margin
        with rasterio.open(item["assets"]["data"]["href"]) as ds:
            c0, r0 = max(c0, 0), max(r0, 0)
            w, h = min(c1, ds.width) - c0, min(r1, ds.height) - r0
            if w <= 0 or h <= 0:
                continue
            scale = min(1.0, p["skraafoto_max_side_px"] / max(w, h))
            shape = (3, max(round(h * scale), 1), max(round(w * scale), 1))
            data = ds.read([1, 2, 3], window=Window(c0, r0, w, h), out_shape=shape, resampling=Resampling.bilinear)
        out.append({"retning": props["direction"], "id": img["id"], "dato": img["dato"], "props": props,
                    "c0": c0, "r0": r0, "w": w, "h": h, "image": np.moveaxis(data, 0, -1),
                    "camera": np.asarray(props["pers:perspective_center"], dtype="float64")})
    return out


def _uv(ob: dict, pts) -> np.ndarray | None:
    """Texture coordinates of ENU points in an oblique crop, or None when a point falls outside it."""
    uv = []
    for x, y, z in pts:
        col, row = project(ob["props"], x, y, z)
        u, v = (col - ob["c0"]) / ob["w"], (row - ob["r0"]) / ob["h"]
        if not (0 <= u <= 1 and 0 <= v <= 1):
            return None
        uv.append((u, v))
    return np.array(uv)


def _texture_split(mesh: Mesh, obliques: list[dict], p: dict, built: int | None = None):
    """Give each triangle the oblique that looks most squarely at it. Returns the untextured rest and textured chunks.

    Occlusion is not handled: a tree or a neighbour in front of a facade ends up painted on it.
    built is the BBR construction year: obliques from that year or earlier are skipped, since the building may not
    have stood yet when they were taken (the photo then shows the ground or the trees there before).
    """
    rest = Mesh()
    groups = {}
    usable = [i for i, ob in enumerate(obliques) if built is None or int(ob["dato"][:4]) > built]
    for a, b, c, n, color in mesh._tris:
        centroid = (a + b + c) / 3
        best, best_score, best_uv = None, p["skraafoto_min_vinkel"], None
        for i in usable:
            ob = obliques[i]
            to_camera = ob["camera"] - centroid
            score = float(np.dot(n, to_camera / np.linalg.norm(to_camera)))
            if score <= best_score:
                continue
            uv = _uv(ob, (a, b, c))
            if uv is not None:
                best, best_score, best_uv = i, score, uv
        if best is None:
            rest._tris.append((a, b, c, n, color))
        else:
            groups.setdefault(best, []).append((a, b, c, n, best_uv))
    chunks = []
    for i, tris in groups.items():
        pos = np.array([v for t in tris for v in t[:3]])
        nrm = np.repeat(np.array([t[3] for t in tris]), 3, axis=0)
        uv = np.vstack([t[4] for t in tris])
        chunks.append((pos, nrm, uv, np.arange(len(pos)), i))
    return rest, chunks


# glTF ----------------------------------------------------------------------------------------------------------

def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    s = c / 255.0
    return np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)


def _glb(parts: list[dict], origin: tuple[float, float, float], root: str, extras: dict,
         images: list[np.ndarray] | None = None) -> bytes:
    """Binary glTF with one node per part. Each part: name, mesh (Mesh), extras (optional), points (optional):
    (positions, colours) drawn as glTF points, and textured (optional): chunks (positions, normals, uv, indices,
    image index) drawn unlit with the given JPEG-encoded images."""
    x0, y0, z0 = origin
    gltf = {"asset": {"version": "2.0", "generator": f"Analyzer {VERSION}", "extras": extras},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"name": root, "children": []}], "meshes": [],
            "materials": [{"name": "vertexfarve", "doubleSided": True,
                           "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0,
                                                    "roughnessFactor": 0.9}}],
            "accessors": [], "bufferViews": []}
    blob = bytearray()

    def raw_view(data: bytes) -> int:
        while len(blob) % 4:
            blob.append(0)
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        return len(gltf["bufferViews"]) - 1

    texture_material = {}
    if images:
        gltf["extensionsUsed"] = ["KHR_materials_unlit"]
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}]
        gltf["images"], gltf["textures"] = [], []
        for i, img in enumerate(images):
            buf = io.BytesIO()
            Image.fromarray(img).save(buf, format="JPEG", quality=85)
            gltf["images"].append({"bufferView": raw_view(buf.getvalue()), "mimeType": "image/jpeg"})
            gltf["textures"].append({"sampler": 0, "source": i})
            gltf["materials"].append({"name": f"foto {i}", "doubleSided": True,
                                      "extensions": {"KHR_materials_unlit": {}},
                                      "pbrMetallicRoughness": {"baseColorTexture": {"index": i},
                                                               "metallicFactor": 0.0, "roughnessFactor": 1.0}})
            texture_material[i] = len(gltf["materials"]) - 1

    def to_gltf(pos, nrm):
        # ENU (east, north, up) -> glTF (x = east, y = up, z = south), relative to the origin
        return (np.column_stack([pos[:, 0] - x0, pos[:, 2] - z0, -(pos[:, 1] - y0)]).astype("float32"),
                np.column_stack([nrm[:, 0], nrm[:, 2], -nrm[:, 1]]).astype("float32"))

    def accessor(arr: np.ndarray, component: int, kind: str, target: int, normalized=False, bounds=False) -> int:
        while len(blob) % 4:
            blob.append(0)
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes, "target": target})
        blob.extend(arr.tobytes())
        acc = {"bufferView": len(gltf["bufferViews"]) - 1, "componentType": component, "count": len(arr),
               "type": kind}
        if normalized:
            acc["normalized"] = True
        if bounds:
            acc["min"], acc["max"] = arr.min(axis=0).tolist(), arr.max(axis=0).tolist()
        gltf["accessors"].append(acc)
        return len(gltf["accessors"]) - 1

    for part in parts:
        primitives = []
        arrays = part["mesh"].arrays()
        if arrays is not None:
            pos, nrm, col, idx = arrays
            gpos, gnrm = to_gltf(pos, nrm)
            rgba = np.column_stack([_srgb_to_linear(col.astype("float64")), np.ones(len(col))])
            gcol = np.round(rgba * 65535).astype("uint16")
            primitives.append({"attributes": {"POSITION": accessor(gpos, 5126, "VEC3", 34962, bounds=True),
                                              "NORMAL": accessor(gnrm, 5126, "VEC3", 34962),
                                              "COLOR_0": accessor(gcol, 5123, "VEC4", 34962, normalized=True)},
                               "indices": accessor(idx.astype("uint32"), 5125, "SCALAR", 34963), "material": 0})
        if part.get("points") is not None:
            pos, col = part["points"]
            gpos, _ = to_gltf(pos, np.zeros_like(pos))
            rgba = np.column_stack([_srgb_to_linear(col.astype("float64")), np.ones(len(col))])
            primitives.append({"attributes": {"POSITION": accessor(gpos, 5126, "VEC3", 34962, bounds=True),
                                              "COLOR_0": accessor(np.round(rgba * 65535).astype("uint16"), 5123,
                                                                  "VEC4", 34962, normalized=True)},
                               "mode": 0, "material": 0})
        for pos, nrm, uv, idx, image in part.get("textured", []):
            gpos, gnrm = to_gltf(pos, nrm)
            primitives.append({"attributes": {"POSITION": accessor(gpos, 5126, "VEC3", 34962, bounds=True),
                                              "NORMAL": accessor(gnrm, 5126, "VEC3", 34962),
                                              "TEXCOORD_0": accessor(uv.astype("float32"), 5126, "VEC2", 34962)},
                               "indices": accessor(idx.astype("uint32"), 5125, "SCALAR", 34963),
                               "material": texture_material[image]})
        if not primitives:
            continue
        gltf["meshes"].append({"name": part["name"], "primitives": primitives})
        node = {"name": part["name"], "mesh": len(gltf["meshes"]) - 1}
        if part.get("extras"):
            node["extras"] = part["extras"]
        gltf["nodes"].append(node)
        gltf["nodes"][0]["children"].append(len(gltf["nodes"]) - 1)
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"] = [{"byteLength": len(blob)}]
    text = json.dumps(gltf, ensure_ascii=False, default=str).encode("utf-8")
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(text), b"JSON") + text
            + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob))


# Output --------------------------------------------------------------------------------------------------------

def write(out_dir: Path, title: str, grid: Grid, parcel, dtm: np.ndarray, dsm: np.ndarray | None,
          classes: np.ndarray, ortho: np.ndarray | None, records: list[dict], obliques: list[dict] | None = None,
          gd_trees: list[dict] | None = None, vegetation: np.ndarray | None = None,
          paving_types: np.ndarray | None = None, terrain_texture: np.ndarray | None = None,
          p: dict = DEFAULTS) -> dict:
    """Write terrain and building models as .glb, model.json and a self-contained model.html. Returns run.json info.

    terrain_texture is a sharper orthophoto on the grid's extent (any resolution) draped on the terrain as a texture;
    without it the run's orthophoto is used.

    obliques are the run's skråfoto entries (id, dato); with them the buildings are also written photo-textured.
    gd_trees are GeoDanmark's Trae/Traegruppe objects and vegetation the point cloud's vegetation points (x, y, z, class).
    paving_types (analyzer.paving) colour the paving in the class-coloured terrain by type.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    inside = classes != UDENFOR
    cx, cy = (parcel.centroid.x, parcel.centroid.y) if parcel is not None else (
        (grid.xmin + grid.xmax) / 2, (grid.ymin + grid.ymax) / 2)
    origin = (round(cx, 2), round(cy, 2), round(float(np.nanmedian(dtm[inside])), 2))
    meta = {"origo_epsg25832_dvr90": list(origin), "akser": "glTF, Y op: x = øst, y = op, z = syd. Enhed: meter"}
    files = {}

    def save(name: str, parts: list[dict], description: str, images: list[np.ndarray] | None = None) -> None:
        parts = [pt for pt in parts if pt["mesh"].arrays() is not None or pt.get("textured") or pt.get("points")]
        if parts:
            (out_dir / name).write_bytes(_glb(parts, origin, name.removesuffix(".glb"),
                                              {**meta, "indhold": description}, images))
            files[name] = description

    step = p["terraen_skridt_px"]
    class_rgb = np.zeros((*classes.shape, 3), dtype="uint8")
    for code, color in COLORS.items():
        class_rgb[classes == code] = color
    if paving_types is not None:
        for code, color in TYPE_COLORS.items():
            class_rgb[paving_types == code] = color
    texture = terrain_texture if terrain_texture is not None else ortho
    if texture is not None:
        hf = _heightfield(dtm, inside, np.zeros((*dtm.shape, 3), dtype="uint8"), grid, step)
        if hf:
            pos, nrm, _, idx = hf
            image, uv = _terrain_texture(texture, grid, inside, pos)
            res_cm = round(grid.res * grid.width / texture.shape[1] * 100)
            save("terraen.glb", [{"name": "terraen", "mesh": Mesh(), "textured": [(pos, nrm, uv, idx, 0)]}],
                 f"Terræn (DHM) med ortofoto som tekstur ({res_cm} cm)", [image])
    for name, colors, description in (("terraen_klasser.glb", class_rgb, "Terræn (DHM) farvet efter arealtype"
                                       + (" og belægningstype (udkast)" if paving_types is not None else "")),):
        if colors is not None:
            mesh = Mesh()
            hf = _heightfield(dtm, inside, colors, grid, step)
            if hf:
                mesh.add(*hf)
            save(name, [{"name": name.removesuffix(".glb"), "mesh": mesh}], description)

    foot = _wall_foot(dtm, grid, p)
    passages = find_passages(records, grid, dsm, p) if dsm is not None else []
    models = [r for r in records if r.get("_roof") is not None] + passages
    place_walls(models, p)
    simple, measured = [], []
    for r in models:
        mesh = Mesh()
        _building_mesh(r, mesh, p, foot)
        simple.append({"name": f"bygning {r['nr']}", "mesh": mesh, "extras": r.get("_extras") or public(r)})
        cells = r.get("_roof_cells")
        if dsm is not None and cells is not None and cells.any():
            rows, cols = np.nonzero(cells)
            window = (rows.min(), rows.max() + 1, cols.min(), cols.max() + 1)
            rr, cc = np.mgrid[window[0]:window[1], window[2]:window[3]]
            x, y = grid.xmin + (cc + 0.5) * grid.res, grid.ymax - (rr + 0.5) * grid.res
            diff = np.full(dsm.shape, np.nan)
            diff[window[0]:window[1], window[2]:window[3]] = (dsm[window[0]:window[1], window[2]:window[3]]
                                                                - _roof_z(r["_roof"], x, y))
            hf = _heightfield(dsm, cells, _deviation_colors(diff, p["afvigelse_skala_m"]), grid, window=window)
            if hf:
                roof_mesh = Mesh()
                roof_mesh.add(*hf)
                measured.append({"name": f"tag målt {r['nr']}", "mesh": roof_mesh,
                                 "extras": {"nr": r["nr"], "model_afvigelse_m": r["model_afvigelse_m"]}})
    save("bygninger.glb", simple, "Bygninger, simpel model: omrids fra GeoDanmark, tagform og højder fra DHM, "
                                  "farver fra BBR (tag og ydervæg)")
    textured_info = None
    if obliques and parcel is not None and simple:
        roofs = [r["_roof"] for r in records if r.get("_roof") is not None]
        z_range = (float(np.nanmin(dtm[inside])), max(rf["ground"] + rf["ridge"] for rf in roofs) + 1.0)
        crops = fetch_obliques(obliques, parcel, z_range, p)
        if crops:
            textured, used = [], set()
            for r in models:
                mesh = Mesh()
                _building_mesh(r, mesh, p, foot)
                built = r["opfoerelsesaar"]
                rest, chunks = _texture_split(mesh, crops, p, int(built) if str(built or "").isdigit() else None)
                used |= {c[4] for c in chunks}
                textured.append({"name": f"bygning {r['nr']}", "mesh": rest, "textured": chunks,
                                 "extras": r.get("_extras") or public(r)})
            save("bygninger_skraafoto.glb", textured, "Bygninger med skråfotos som tekstur (hver flade får det billede, "
                 "der ser mest vinkelret på den). Træer og naboer foran en facade kommer med på facaden. "
                 "Bygninger opført i skråfotoets år eller senere har BBR-farver",
                 [c["image"] for c in crops])
            textured_info = [{"retning": c["retning"], "id": c["id"], "dato": c["dato"], "brugt": i in used}
                             for i, c in enumerate(crops)]
    save("tag_maalt.glb", measured, "Tagflader målt i DHM overflade, farvet efter afvigelse fra den simple model "
                                    f"(rød = højere, blå = lavere, fuld farve ved {p['afvigelse_skala_m']} m)")

    trees = find_trees(gd_trees or [], grid, parcel, dtm, dsm, classes, ortho, p)
    tree_parts = []
    for t in trees:
        mesh = Mesh()
        _tree_mesh(t, mesh)
        tree_parts.append({"name": f"træ {t['nr']}", "mesh": mesh,
                           "extras": {"trae": t["nr"], **{k: v for k, v in t.items() if k not in ("nr", "farve")}}})
    save("traeer.glb", tree_parts, "Træer, simpel model: stamme og krone pr. træ. Position fra GeoDanmark og toppe i "
                                   "højdemodellen, højde og kronebredde målt i højdemodellen, farve fra ortofotoet")
    points = _vegetation_points(vegetation, parcel, grid, ortho, p) if vegetation is not None and len(vegetation) else None
    if points is not None:
        save("traeer_maalt.glb", [{"name": "vegetationspunkter", "mesh": Mesh(), "points": points}],
             f"Træer og buske målt: punktskyens vegetationspunkter ({len(points[0]):,} stk.) farvet fra ortofotoet"
             .replace(",", "."))

    meta["filer"] = files
    meta["parametre"] = p
    (out_dir / "model.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {name: base64.b64encode((out_dir / name).read_bytes()).decode("ascii") for name in files}
    html = (MODEL_TEMPLATE.replace("__TITLE__", title)
            .replace("__DATA__", json.dumps({"files": files, "glb": payload, "meta": meta}, ensure_ascii=False)))
    (out_dir / "model.html").write_text(html, encoding="utf-8")
    return {"origo_epsg25832_dvr90": list(origin), "filer": list(files), "bygninger": len(simple) - len(passages),
            "gennemgange": [r["_extras"] for r in passages],
            "tage_maalt": len(measured), "skraafotos": textured_info,
            "traeer": {"i_alt": len(trees), "geodanmark": sum(t["kilde"] == "GeoDanmark" for t in trees),
                       "hoejdemodel": sum(t["kilde"] == "Højdemodel" for t in trees),
                       "vegetationspunkter": len(points[0]) if points is not None else 0}}


MODEL_TEMPLATE = """<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#f6f6f4; --panel:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#e2e2dc; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161615; --panel:#20201e; --text:#ececea; --muted:#9a9a94; --line:#33332f; } }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 system-ui, sans-serif; background:var(--bg); color:var(--text);
         display:grid; grid-template-columns: 300px 1fr; height:100vh; }
  aside { background:var(--panel); border-right:1px solid var(--line); padding:16px; overflow:auto; }
  h1 { font-size:16px; margin:0 0 4px; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:18px 0 8px; }
  .muted { color:var(--muted); font-size:12px; }
  label { display:flex; gap:8px; align-items:flex-start; padding:3px 0; cursor:pointer; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  td { padding:2px 0; border-bottom:1px solid var(--line); vertical-align:top; } td:first-child { color:var(--muted); padding-right:8px; }
  #view { position:relative; } canvas { display:block; }
  @media (max-width: 700px) { body { grid-template-columns: 1fr; grid-template-rows: auto 60vh; height:auto; } }
</style>
</head>
<body>
<aside>
  <a class="muted" href="../../../index.html">← Alle ejendomme</a> · <a class="muted" href="../../viewer.html">Kort</a>
  <h1>__TITLE__</h1>
  <div class="muted">3D-model af ejendommen. Træk for at dreje, scroll for at zoome, højreklik og træk for at flytte.</div>
  <h2>Lag</h2>
  <div id="layers"></div>
  <h2>Valgt</h2>
  <div id="info" class="muted">Klik på en bygning eller et træ.</div>
  <h2>Om modellen</h2>
  <div class="muted" id="about"></div>
</aside>
<div id="view"></div>
<script type="importmap">
{ "imports": { "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
               "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/" } }
</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const DATA = __DATA__;
const view = document.getElementById("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
view.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xdfe6ec);
const camera = new THREE.PerspectiveCamera(45, 1, 0.5, 5000);
const controls = new OrbitControls(camera, renderer.domElement);
scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a80, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.8);
sun.position.set(-60, 120, 80);  // from the south-west, high
scene.add(sun);

function resize() {
  const w = view.clientWidth, h = view.clientHeight || window.innerHeight;
  renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);

const loader = new GLTFLoader();
const groups = {};
const toBuffer = b64 => Uint8Array.from(atob(b64), c => c.charCodeAt(0)).buffer;
const textured = "bygninger_skraafoto.glb" in DATA.glb;
const defaults = { "terraen.glb": true, "terraen_klasser.glb": false, "bygninger.glb": !textured,
                   "bygninger_skraafoto.glb": true, "tag_maalt.glb": false, "traeer.glb": true,
                   "traeer_maalt.glb": false };
// Alternative looks of the same surface: only one of each group is shown at a time
const exclusiveGroups = [["terraen.glb", "terraen_klasser.glb"], ["bygninger.glb", "bygninger_skraafoto.glb"],
                         ["traeer.glb", "traeer_maalt.glb"]];

await Promise.all(Object.entries(DATA.glb).map(([name, b64]) => new Promise(done =>
  loader.parse(toBuffer(b64), "", gltf => {
    // Point clouds load as 1 px points; give them a size in metres
    gltf.scene.traverse(obj => { if (obj.isPoints) { obj.material.size = 0.25; obj.material.sizeAttenuation = true; } });
    groups[name] = gltf.scene; scene.add(gltf.scene); done();
  }))));

const layers = document.getElementById("layers");
for (const [name, text] of Object.entries(DATA.files)) {
  const on = defaults[name] ?? true;
  groups[name].visible = on;
  const label = document.createElement("label");
  label.innerHTML = `<input type="checkbox" ${on ? "checked" : ""}><span>${text}</span>`;
  const box = label.querySelector("input");
  box.addEventListener("change", () => {
    groups[name].visible = box.checked;
    const group = exclusiveGroups.find(g => g.includes(name));
    if (box.checked && group) for (const other of group) if (other !== name && groups[other]) {
      groups[other].visible = false;
      layers.querySelector(`[data-name="${other}"]`).checked = false;
    }
  });
  box.dataset.name = name;
  layers.appendChild(label);
}
const o = DATA.meta.origo_epsg25832_dvr90;
document.getElementById("about").textContent = `${DATA.meta.akser}. Origo: ${o[0]} Ø, ${o[1]} N, ${o[2]} m (DVR90), ` +
  "i EPSG:25832. Den simple model er en baseline: tagform og højder er estimeret fra Danmarks Højdemodel, " +
  "farver fra BBR. Den målte model viser tagfladen direkte fra højdemodellen. Træernes kronebund er antaget; " +
  "laserscanning og ortofoto er fra foråret, så kronerne kan være uden blade.";

const bounds = new THREE.Box3();
for (const g of Object.values(groups)) bounds.expandByObject(g);
const centre = bounds.getCenter(new THREE.Vector3()), size = bounds.getSize(new THREE.Vector3()).length();
controls.target.copy(centre);
camera.position.copy(centre).add(new THREE.Vector3(-0.35, 0.55, 0.6).multiplyScalar(size));
controls.update();

const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
const info = document.getElementById("info");
const LABELS = { anvendelse: "Anvendelse", opfoerelsesaar: "Opført", etager: "Etager", tagmateriale: "Tagmateriale",
  ydervaeg: "Ydervæg", tagform: "Tagform (målt)", taghaeldning_grader: "Taghældning (°)", model_tagform: "Tagform i model",
  model_tagfod_m: "Tagfod (m over terræn)", model_tagryg_m: "Tagryg (m over terræn)",
  model_afvigelse_m: "Afvigelse fra målt tag (m)", grundplan_m2: "Grundplan (m²)", tagareal_m2: "Tagareal (m²)" };
const TREE_LABELS = { kilde: "Fundet i", hoejde_m: "Højde (m)", hoejde_maalt: "Højde målt i højdemodel",
  krone_diameter_m: "Kronediameter (m)", kronebund_m: "Kronebund (m, antaget)" };
renderer.domElement.addEventListener("click", e => {
  const r = renderer.domElement.getBoundingClientRect();
  mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(mouse, camera);
  ray.params.Points.threshold = 0.2;
  const hit = ray.intersectObjects(Object.values(groups).filter(g => g.visible), true)
    .find(h => h.object.userData && (h.object.userData.nr !== undefined || h.object.userData.trae !== undefined));
  if (!hit) { info.textContent = "Klik på en bygning eller et træ."; return; }
  const d = hit.object.userData;
  const [heading, labels] = d.trae !== undefined ? [`Træ ${d.trae}`, TREE_LABELS] : [`Bygning ${d.nr}`, LABELS];
  info.innerHTML = `<b>${heading}</b><table>` + Object.entries(labels)
    .filter(([k]) => d[k] !== undefined && d[k] !== null)
    .map(([k, t]) => `<tr><td>${t}</td><td>${d[k]}</td></tr>`).join("") + "</table>";
});

resize();
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
</script>
</body>
</html>
"""
