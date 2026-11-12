"""Facade splat (optional): a Gaussian splat made from ground-level photos, placed on the 3D model.

The splat comes from AnySplat (see sideprojekt_3d/anysplat/), run on 5-20 photos around the house. It lies in
AnySplat's own frame: arbitrary origin, orientation and scale. This module places it in EPSG:25832/DVR90:

1. Up: the photos are taken at eye height while walking around the house, so the camera centres lie in a
   horizontal plane. Its normal is up (sign from the cameras' own up vectors).
2. Ground: the lowest splats under each camera (a low percentile, so the odd stray splat under ground is ignored).
3. Walls: top-down cells holding splats over a tall height span between ground and eaves. Trees and fences
   show up too; they are outliers below.
4. Fit in the plane: a 2D similarity transform (rotation, scale, shift) of the wall cells onto the model's wall
   outlines (after the roof overhang is set back). Many starting rotations and scales are tried; each is refined with
   trimmed ICP. A fit is scored by how many wall cells land on a model wall (precision) and how much of the model's
   walls is covered (coverage), and penalised for each camera that lands inside a building.
5. Height: the splat's ground is put on the DHM terrain under the cameras, with the same scale as in the plane.

The splat is not metric: AnySplat works on 448x448 px crops, so walls can bend a little and corners are not quite
square. One similarity transform cannot fit every wall; the quality numbers in run.json show how well it fits.
A placering.json next to the splat adjusts the automatic placement by hand (turn, shift, scale).

Used only for looking at the model. Never for measuring.
"""

import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from shapely.geometry import Point
from shapely.ops import unary_union

from analyzer.grid import Grid

DEFAULTS = {
    "splat_kamera_hoejde_m": 1.6,      # first guess of the camera height above ground (photos taken at eye height)
    "splat_jord_radius": 0.2,          # ground is looked for under each camera within this share of the camera ring
    "splat_jord_percentil": 3,         # ground = this low percentile of splat heights under the camera
    "splat_min_opacitet_tilpasning": 0.5,  # only fairly opaque splats are used for the fit
    "splat_vaeg_celle_m": 0.15,        # top-down cell size for finding walls
    "splat_vaeg_hoejde_m": (0.3, 2.5),  # splat heights above ground used for walls (below the eaves)
    "splat_vaeg_min_spaend_m": 1.2,    # a cell is wall when its splats span at least this height
    "splat_start_rotation_grader": 4,  # starting rotations tried, every n degrees
    "splat_start_skala": (0.8, 1.0, 1.2, 1.4, 1.6),  # starting scales tried, times the first guess
    "splat_skala_graense": (0.5, 2.0),  # the fit may not move the scale further than this from its start
    "splat_inlier_m": 0.3,             # a wall cell this close to a model wall fits it
    "splat_straf_kamera_inde": 0.1,    # score penalty for each camera inside a building
    "splat_min_opacitet_vis": 0.05,    # fainter splats are dropped from the view
    "splat_max_afstand_m": 25.0,       # splats further than this from the buildings are dropped (sky, neighbours)
    "splat_max_over_tag_m": 3.0,       # and splats higher than this above the highest ridge (sky)
    "splat_max_antal": 700_000,        # at most this many splats in model.html (the most opaque are kept)
}

SH_C0 = 0.28209479177387814  # zeroth spherical-harmonics band: colour = 0.5 + SH_C0 * f_dc
GLTF = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])  # EPSG:25832 (x east, y north, z up) to glTF (x, up, south)


def find(folder: Path) -> Path | None:
    """The AnySplat output folder when it holds a splat and its cameras, else None."""
    if (folder / "gaussians.ply").is_file() and (folder / "cameras.json").is_file():
        return folder
    return None


def read_ply(path: Path) -> np.ndarray:
    """Binary little-endian PLY with float32 vertex properties, as AnySplat writes it."""
    with open(path, "rb") as f:
        names, n = [], 0
        while (line := f.readline().decode("ascii").strip()) != "end_header":
            if line.startswith("format") and "binary_little_endian" not in line:
                raise ValueError(f"PLY skal være binary_little_endian: {line}")
            if line.startswith("element vertex"):
                n = int(line.split()[-1])
            elif line.startswith("property"):
                if line.split()[1] != "float":
                    raise ValueError(f"PLY-egenskab er ikke float: {line}")
                names.append(line.split()[-1])
        return np.frombuffer(f.read(), dtype=[(k, "<f4") for k in names], count=n)


def _similarity_2d(a: np.ndarray, b: np.ndarray):
    """Least-squares similarity (scale, rotation, shift) taking points a onto b (Umeyama)."""
    ma, mb = a.mean(0), b.mean(0)
    a0, b0 = a - ma, b - mb
    u, s, vt = np.linalg.svd(b0.T @ a0 / len(a))
    d = np.eye(2)
    if np.linalg.det(u @ vt) < 0:
        d[1, 1] = -1
    rot = u @ d @ vt
    scale = np.trace(np.diag(s) @ d) / a0.var(0).sum()
    return scale, rot, mb - scale * rot @ ma


def _rot2(deg: float) -> np.ndarray:
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])


def _wall_cells(h: np.ndarray, height: np.ndarray, cell: float, span: float) -> np.ndarray:
    """Centres of top-down cells whose points span at least `span` in height. h: N x 2, same unit as cell."""
    ij = np.floor(h / cell).astype(np.int64)
    cells, inv, counts = np.unique(ij, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    lo = np.full(len(cells), np.inf)
    hi = np.full(len(cells), -np.inf)
    np.minimum.at(lo, inv, height)
    np.maximum.at(hi, inv, height)
    keep = (counts >= 5) & (hi - lo >= span)
    return (cells[keep] + 0.5) * cell


def _terrain(dtm: np.ndarray, grid: Grid, xy: np.ndarray) -> np.ndarray:
    col = np.floor((xy[:, 0] - grid.xmin) / grid.res).astype(int)
    row = np.floor((grid.ymax - xy[:, 1]) / grid.res).astype(int)
    ok = (row >= 0) & (row < dtm.shape[0]) & (col >= 0) & (col < dtm.shape[1])
    z = np.full(len(xy), np.nan)
    z[ok] = dtm[row[ok], col[ok]]
    return z


def place(splat: np.ndarray, cameras: list[dict], walls: list, footprints: list, dtm: np.ndarray, grid: Grid,
          adjust: dict | None = None, p: dict = DEFAULTS) -> dict:
    """Transform from AnySplat's frame to EPSG:25832/DVR90: x' = scale * rotation @ x + shift. Plus fit quality.

    walls are the wall outlines (Polygons) of the buildings that have walls; footprints all buildings' outlines
    (cameras must stand outside them). adjust is the hand adjustment from placering.json.
    """
    pts = np.stack([splat["x"], splat["y"], splat["z"]], 1).astype(float)
    ext = np.array([c["extrinsic"] for c in cameras], dtype=float)  # camera-to-world, OpenCV (y down, z forward)
    centres = ext[:, :3, 3]

    # 1. Up: normal of the plane through the camera centres, pointing the way the cameras' up (-y) points
    _, sv, vt = np.linalg.svd(centres - centres.mean(0))
    up = vt[2] * np.sign(vt[2] @ -ext[:, :3, 1].mean(0))
    e1 = np.cross(up, [1.0, 0, 0] if abs(up[0]) < 0.9 else [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    level = np.stack([e1, np.cross(up, e1), up])  # rows: two horizontal axes and up (right-handed)
    ph, ch = pts @ level.T, centres @ level.T

    # 2. Ground under the cameras
    fit = splat["opacity"] >= p["splat_min_opacitet_tilpasning"]
    ring = np.median(np.linalg.norm(ch[:, :2] - ch[:, :2].mean(0), axis=1))
    tree = cKDTree(ph[fit, :2])
    grounds = [np.percentile(ph[fit][idx, 2], p["splat_jord_percentil"])
               for idx in tree.query_ball_point(ch[:, :2], p["splat_jord_radius"] * ring) if len(idx) > 100]
    if not grounds:
        raise ValueError("ingen splats under kameraerne, så jorden kan ikke findes")
    ground = float(np.median(grounds))
    cam_h = float(np.median(ch[:, 2] - ground))
    s0 = p["splat_kamera_hoejde_m"] / cam_h  # metres per splat unit, first guess

    # 3. Wall cells (in metres at the first-guess scale)
    hm = (ph[:, 2] - ground) * s0
    lo, hi = p["splat_vaeg_hoejde_m"]
    sel = fit & (hm > lo) & (hm < hi)
    cells = _wall_cells(ph[sel, :2] * s0, hm[sel], p["splat_vaeg_celle_m"], p["splat_vaeg_min_spaend_m"])
    if len(cells) < 30:
        raise ValueError(f"for få vægge i splatten ({len(cells)} celler)")

    # 4. Fit in the plane
    outline = unary_union(walls).boundary
    model = np.array([outline.interpolate(t).coords[0] for t in np.arange(0, outline.length, 0.05)])
    model_tree = cKDTree(model)
    buildings = unary_union(footprints)
    cams = ch[:, :2] * s0
    inlier = p["splat_inlier_m"]
    smin, smax = p["splat_skala_graense"]

    def score(sc, rot, shift):
        q = sc * cells @ rot.T + shift
        precision = float((model_tree.query(q)[0] < inlier).mean())
        coverage = float((cKDTree(q).query(model)[0] < inlier).mean())
        inside = sum(buildings.contains(Point(c)) for c in sc * cams @ rot.T + shift)
        f1 = 2 * precision * coverage / (precision + coverage) if precision + coverage else 0.0
        return f1 - p["splat_straf_kamera_inde"] * inside, precision, coverage, inside

    centre = np.array(unary_union(walls).centroid.coords[0])
    best = None
    for deg in np.arange(0, 360, p["splat_start_rotation_grader"]):
        for start in p["splat_start_skala"]:
            sc, rot = start, _rot2(deg)
            shift = centre - sc * rot @ cells.mean(0)
            for thr in (3.0, 2.0, 1.5, 1.0, 0.7, 0.5, 0.4, inlier, inlier, inlier):
                d, j = model_tree.query(sc * cells @ rot.T + shift)
                near = d < thr
                if near.sum() < 30:
                    break
                sc, rot, shift = _similarity_2d(cells[near], model[j[near]])
                sc = float(np.clip(sc, start * smin, start * smax))
            result = score(sc, rot, shift)
            if best is None or result[0] > best[0][0]:
                best = (result, sc, rot, shift)
    (total, precision, coverage, inside), sc, rot, shift = best
    in_wall = model_tree.query(sc * cells @ rot.T + shift)[0]
    rms = float(np.sqrt(np.mean(in_wall[in_wall < inlier] ** 2))) if (in_wall < inlier).any() else None

    # 5. Height: the splat's ground on the terrain under the cameras
    scale = sc * s0
    cam_xy = sc * cams @ rot.T + shift
    terrain = _terrain(dtm, grid, cam_xy)
    z0 = float(np.nanmedian(terrain)) if np.isfinite(terrain).any() else float(np.nanmedian(dtm))

    rot3 = np.eye(3)
    rot3[:2, :2] = rot
    rotation = rot3 @ level
    offset = np.array([shift[0], shift[1], z0 - scale * ground])

    adjust = adjust or {}
    if adjust:
        # Hand adjustment about the middle of the walls: turn (degrees, counter-clockwise seen from above),
        # scale factor, then shift (east, north, up in metres)
        turn = np.eye(3)
        turn[:2, :2] = _rot2(float(adjust.get("drej_grader", 0)))
        factor = float(adjust.get("skala_faktor", 1))
        pivot = np.array([centre[0], centre[1], z0])
        rotation = turn @ rotation
        offset = factor * (turn @ (offset - pivot)) + pivot + np.array(adjust.get("flyt_m", [0, 0, 0]), dtype=float)
        scale *= factor

    cam_world = scale * centres @ rotation.T + offset
    cam_height = cam_world[:, 2] - _terrain(dtm, grid, cam_world[:, :2])
    return {
        "scale": scale, "rotation": rotation, "offset": offset,
        "info": {
            "skala_m_pr_enhed": round(scale, 3),
            "drejning_grader": round(float(np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0]))), 1),
            "praecision": round(precision, 3), "daekning": round(coverage, 3),
            "rms_m": round(rms, 3) if rms is not None else None,
            "kameraer_i_bygning": int(inside),
            "kamera_hoejde_over_terraen_m": round(float(np.nanmedian(cam_height)), 2),
            "kamera_plan_fladhed": round(float(sv[2] / sv[0]), 3),
            "vaegceller": len(cells),
            "haandjustering": adjust or None,
            "matrix_epsg25832_dvr90": np.vstack([np.hstack([scale * rotation, offset[:, None]]), [0, 0, 0, 1]])
            .round(6).tolist(),
        },
        "cameras": [{"billede": c.get("image"), "oest": round(float(w[0]), 2), "nord": round(float(w[1]), 2),
                     "hoejde": round(float(w[2]), 2)} for c, w in zip(cameras, cam_world)],
    }


def encode(splat: np.ndarray, placement: dict, origin: tuple[float, float, float], keep_near, z_max: float,
           p: dict = DEFAULTS) -> tuple[bytes, int]:
    """The placed splat in the .splat format (32 bytes per splat) in the model's glTF frame.

    keep_near is a shapely geometry; splats further from it than splat_max_afstand_m are dropped, as are splats above
    z_max (DVR90) and faint ones. Layout per splat: position float32 x3, scale float32 x3 (linear),
    colour RGBA uint8 x4, rotation uint8 x4 (w, x, y, z as 128 + 128 * q).
    """
    scale, rotation, offset = placement["scale"], placement["rotation"], placement["offset"]
    pts = np.stack([splat["x"], splat["y"], splat["z"]], 1).astype(float) @ (scale * rotation).T + offset
    opacity = splat["opacity"].astype(float)  # AnySplat writes opacity after the sigmoid (0..1), not as a logit
    keep = (opacity >= p["splat_min_opacitet_vis"]) & (pts[:, 2] <= z_max)
    near = keep_near.buffer(p["splat_max_afstand_m"]).envelope.bounds
    keep &= ((pts[:, 0] >= near[0]) & (pts[:, 0] <= near[2]) & (pts[:, 1] >= near[1]) & (pts[:, 1] <= near[3]))
    idx = np.nonzero(keep)[0]
    if len(idx) > p["splat_max_antal"]:
        idx = idx[np.argsort(-opacity[idx])[:p["splat_max_antal"]]]

    world = to_gltf(pts[idx], origin)
    sizes = np.exp(np.stack([splat["scale_0"], splat["scale_1"], splat["scale_2"]], 1)[idx].astype(float)) * scale
    rgb = 0.5 + SH_C0 * np.stack([splat["f_dc_0"], splat["f_dc_1"], splat["f_dc_2"]], 1)[idx].astype(float)
    rgba = np.clip(np.hstack([rgb, opacity[idx, None]]) * 255, 0, 255).round().astype(np.uint8)
    wxyz = np.stack([splat["rot_0"], splat["rot_1"], splat["rot_2"], splat["rot_3"]], 1)[idx].astype(float)
    turn = Rotation.from_matrix(GLTF @ rotation) * Rotation.from_quat(wxyz[:, [1, 2, 3, 0]])
    xyzw = turn.as_quat()
    quat = np.clip(np.round(xyzw[:, [3, 0, 1, 2]] * 128 + 128), 0, 255).astype(np.uint8)

    out = np.empty(len(idx), dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    out["pos"], out["scale"], out["rgba"], out["rot"] = world, sizes, rgba, quat
    return out.tobytes(), len(idx)


def to_gltf(xyz: np.ndarray, origin: tuple[float, float, float]) -> np.ndarray:
    """EPSG:25832/DVR90 to the model's glTF frame (metres from origin; x east, y up, z south)."""
    return (xyz - np.asarray(origin)) @ GLTF.T


def build(folder: Path, footprints: list, walls: list, dtm: np.ndarray, grid: Grid,
          origin: tuple[float, float, float], z_max: float, p: dict = DEFAULTS) -> tuple[bytes | None, dict]:
    """Place the splat in `folder` on the model's buildings. Returns (.splat bytes or None, info for run.json).

    footprints are all buildings' 3D outlines, walls the wall outlines of buildings that have walls (not open ones).
    """
    if not walls:
        return None, {"status": "ingen bygninger med vægge at placere splatten på"}
    splat = read_ply(folder / "gaussians.ply")
    cameras = json.loads((folder / "cameras.json").read_text(encoding="utf-8"))["cameras"]
    adjust_path = folder / "placering.json"
    adjust = json.loads(adjust_path.read_text(encoding="utf-8")) if adjust_path.is_file() else None
    placement = place(splat, cameras, walls, footprints, dtm, grid, adjust, p)
    data, count = encode(splat, placement, origin, unary_union(footprints), z_max, p)
    info = {"status": "ok", "mappe": "/".join(folder.parts[-3:]), "billeder": len(cameras), "splats_i_alt": len(splat),
            "splats_vist": count, **placement["info"], "kameraer": placement["cameras"]}
    return data, info
