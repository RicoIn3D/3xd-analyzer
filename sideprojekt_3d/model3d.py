"""3D model of the property for Unity and the browser: terrain and buildings as binary glTF (.glb).

Buildings come in two variants:
- simple: footprint walls with a parametric roof (flat, gable, hip, mono-pitch or barrel) built from the roof
  shape, pitch and heights that buildings.py estimates from the DHM surface model. A clean baseline.
- measured: the roof surface taken straight from the DHM surface model, only where the point cloud sees the roof.
  Coloured by its deviation from the simple model, so the baseline can be checked.

Units are metres. The origin is the property centroid at median ground level (written to model.json).
glTF is Y-up and right-handed: x = east, y = up, z = south.
"""

import base64
import json
import math
import struct
from pathlib import Path

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

from analyzer import VERSION
from analyzer.buildings import NO_BBR_COLOR, TAG_COLORS, public
from analyzer.classes import COLORS, UDENFOR
from analyzer.grid import Grid

DEFAULTS = {
    "terraen_skridt_px": 2,       # terrain mesh uses every n-th grid cell (0.5 m on the 0.25 m grid)
    "min_tagfod_m": 2.0,          # eaves are never lower than this above ground
    "etagehoejde_m": 3.0,         # height per BBR storey when the surface model has no height for a building
    "tondetag_strimler": 6,       # strips per roof side approximating a barrel roof
    "afvigelse_skala_m": 1.0,     # deviation shown as full red (roof higher) or blue (lower) in the measured roof
    "tilpasning_min_celler": 20,  # roof heights are fitted to the surface model when this many roof cells are seen
    "geodanmark_max_noejagtighed_m": 0.5,  # GeoDanmark roof-edge height is used when measured at least this precisely
    "tilpasning_afviger_m": 0.5,  # cells further than this from the fit (chimneys, trees) are dropped and refitted
}

# BBR byg032 wall material
WALL_COLORS = {
    "1": (165, 85, 60), "2": (210, 205, 195), "3": (225, 150, 60), "4": (205, 185, 150), "5": (150, 110, 75),
    "6": (185, 185, 180), "8": (125, 135, 145), "10": (195, 195, 185), "11": (232, 232, 232), "12": (150, 210, 230),
}
DEFAULT_WALL_COLOR = (215, 205, 190)

UP = np.array([0.0, 0.0, 1.0])


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
            if (r["geodanmark_tagkant_m"] is not None and accuracy is not None
                    and float(accuracy) <= p["geodanmark_max_noejagtighed_m"]
                    and r["geodanmark_hoejde_metode"] != "Manuel"):
                h_med = h_max = r["geodanmark_tagkant_m"]
                kind, label = "flat", "fladt i tagkant (højde fra GeoDanmark)"
            else:
                floors = int(r["etager"]) if str(r["etager"] or "").isdigit() else 1
                h_med = h_max = max(floors, 1) * p["etagehoejde_m"]
                kind, label = "flat", "fladt (højde fra BBR-etager)"
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
    eaves = max(float(eaves), p["min_tagfod_m"])
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


def _roof_z(roof: dict, x, y):
    """Absolute roof height at world coordinates x, y."""
    return roof["ground"] + roof["eaves"] + (roof["ridge"] - roof["eaves"]) * _roof_shape(roof, x, y)


def _regions(roof: dict, strips: int) -> list[Polygon]:
    """Parts of the roof's rectangle in which the roof is planar (or nearly, for a barrel), in world coordinates."""
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


def _building_mesh(r: dict, mesh: Mesh, p: dict) -> None:
    roof = r["_roof"]
    footprints = [orient(g, 1.0) for g in _polygons(r["_geom"])]
    roof_color = (TAG_COLORS.get(str(r["tagmateriale_kode"]), NO_BBR_COLOR) if r["bbr_fundet"] else NO_BBR_COLOR)
    wall_color = WALL_COLORS.get(str(r["_wall_code"]), DEFAULT_WALL_COLOR)
    base = r["_base_z"] if r["_base_z"] is not None else roof["ground"]
    pieces = [pc for geom in footprints for region in _regions(roof, p["tondetag_strimler"])
              for pc in _polygons(geom.intersection(region))]
    corners = []
    for piece in pieces:
        for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(piece)):
            xy = np.asarray(tri.exterior.coords)[:3]
            z = _roof_z(roof, xy[:, 0], xy[:, 1])
            mesh.tri(*np.column_stack([xy, z]), roof_color, facing=UP)
        corners += [np.asarray(ring.coords) for ring in (piece.exterior, *piece.interiors)]
    extra = np.vstack(corners) if corners else np.empty((0, 2))
    for ring in (ring for geom in footprints for ring in (geom.exterior, *geom.interiors)):
        pts = _densify(np.asarray(ring.coords), extra)
        top = _roof_z(roof, pts[:, 0], pts[:, 1])
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            outward = np.array([y1 - y0, x0 - x1, 0.0])  # exterior is counter-clockwise, holes clockwise
            q = [(x0, y0, base), (x1, y1, base), (x1, y1, top[i + 1]), (x0, y0, top[i])]
            mesh.tri(q[0], q[1], q[2], wall_color, facing=outward)
            mesh.tri(q[0], q[2], q[3], wall_color, facing=outward)


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


# glTF ----------------------------------------------------------------------------------------------------------

def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    s = c / 255.0
    return np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)


def _glb(parts: list[dict], origin: tuple[float, float, float], root: str, extras: dict) -> bytes:
    """Binary glTF with one node per part. Each part: name, mesh (Mesh), extras (optional)."""
    x0, y0, z0 = origin
    gltf = {"asset": {"version": "2.0", "generator": f"Analyzer {VERSION}", "extras": extras},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"name": root, "children": []}], "meshes": [],
            "materials": [{"name": "vertexfarve", "doubleSided": True,
                           "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0,
                                                    "roughnessFactor": 0.9}}],
            "accessors": [], "bufferViews": []}
    blob = bytearray()

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
        arrays = part["mesh"].arrays()
        if arrays is None:
            continue
        pos, nrm, col, idx = arrays
        # ENU (east, north, up) -> glTF (x = east, y = up, z = south), relative to the origin
        gpos = np.column_stack([pos[:, 0] - x0, pos[:, 2] - z0, -(pos[:, 1] - y0)]).astype("float32")
        gnrm = np.column_stack([nrm[:, 0], nrm[:, 2], -nrm[:, 1]]).astype("float32")
        rgba = np.column_stack([_srgb_to_linear(col.astype("float64")), np.ones(len(col))])
        gcol = np.round(rgba * 65535).astype("uint16")
        attributes = {"POSITION": accessor(gpos, 5126, "VEC3", 34962, bounds=True),
                      "NORMAL": accessor(gnrm, 5126, "VEC3", 34962),
                      "COLOR_0": accessor(gcol, 5123, "VEC4", 34962, normalized=True)}
        indices = accessor(idx.astype("uint32"), 5125, "SCALAR", 34963)
        gltf["meshes"].append({"name": part["name"], "primitives": [
            {"attributes": attributes, "indices": indices, "material": 0}]})
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
          classes: np.ndarray, ortho: np.ndarray | None, records: list[dict], p: dict = DEFAULTS) -> dict:
    """Write terrain and building models as .glb, model.json and a self-contained model.html. Returns run.json info."""
    out_dir.mkdir(parents=True, exist_ok=True)
    inside = classes != UDENFOR
    cx, cy = (parcel.centroid.x, parcel.centroid.y) if parcel is not None else (
        (grid.xmin + grid.xmax) / 2, (grid.ymin + grid.ymax) / 2)
    origin = (round(cx, 2), round(cy, 2), round(float(np.nanmedian(dtm[inside])), 2))
    meta = {"origo_epsg25832_dvr90": list(origin), "akser": "glTF, Y op: x = øst, y = op, z = syd. Enhed: meter"}
    files = {}

    def save(name: str, parts: list[dict], description: str) -> None:
        parts = [pt for pt in parts if pt["mesh"].arrays() is not None]
        if parts:
            (out_dir / name).write_bytes(_glb(parts, origin, name.removesuffix(".glb"), {**meta, "indhold": description}))
            files[name] = description

    step = p["terraen_skridt_px"]
    class_rgb = np.zeros((*classes.shape, 3), dtype="uint8")
    for code, color in COLORS.items():
        class_rgb[classes == code] = color
    for name, colors, description in (("terraen.glb", ortho, "Terræn (DHM) med ortofoto-farver"),
                                      ("terraen_klasser.glb", class_rgb, "Terræn (DHM) farvet efter arealtype")):
        if colors is not None:
            mesh = Mesh()
            hf = _heightfield(dtm, inside, colors, grid, step)
            if hf:
                mesh.add(*hf)
            save(name, [{"name": name.removesuffix(".glb"), "mesh": mesh}], description)

    simple, measured = [], []
    for r in records:
        if r.get("_roof") is None:
            continue
        mesh = Mesh()
        _building_mesh(r, mesh, p)
        simple.append({"name": f"bygning {r['nr']}", "mesh": mesh, "extras": public(r)})
        cells = r["_roof_cells"]
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
    save("tag_maalt.glb", measured, "Tagflader målt i DHM overflade, farvet efter afvigelse fra den simple model "
                                    f"(rød = højere, blå = lavere, fuld farve ved {p['afvigelse_skala_m']} m)")

    meta["filer"] = files
    meta["parametre"] = p
    (out_dir / "model.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {name: base64.b64encode((out_dir / name).read_bytes()).decode("ascii") for name in files}
    html = (MODEL_TEMPLATE.replace("__TITLE__", title)
            .replace("__DATA__", json.dumps({"files": files, "glb": payload, "meta": meta}, ensure_ascii=False)))
    (out_dir / "model.html").write_text(html, encoding="utf-8")
    return {"origo_epsg25832_dvr90": list(origin), "filer": list(files), "bygninger": len(simple),
            "tage_maalt": len(measured)}


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
  <h1>__TITLE__</h1>
  <div class="muted">3D-model af ejendommen. Træk for at dreje, scroll for at zoome, højreklik og træk for at flytte.</div>
  <h2>Lag</h2>
  <div id="layers"></div>
  <h2>Bygning</h2>
  <div id="info" class="muted">Klik på en bygning.</div>
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
const defaults = { "terraen.glb": true, "terraen_klasser.glb": false, "bygninger.glb": true, "tag_maalt.glb": false };
const exclusive = ["terraen.glb", "terraen_klasser.glb"];  // two colourings of the same surface

await Promise.all(Object.entries(DATA.glb).map(([name, b64]) => new Promise(done =>
  loader.parse(toBuffer(b64), "", gltf => { groups[name] = gltf.scene; scene.add(gltf.scene); done(); }))));

const layers = document.getElementById("layers");
for (const [name, text] of Object.entries(DATA.files)) {
  const on = defaults[name] ?? true;
  groups[name].visible = on;
  const label = document.createElement("label");
  label.innerHTML = `<input type="checkbox" ${on ? "checked" : ""}><span>${text}</span>`;
  const box = label.querySelector("input");
  box.addEventListener("change", () => {
    groups[name].visible = box.checked;
    if (box.checked && exclusive.includes(name)) for (const other of exclusive) if (other !== name && groups[other]) {
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
  "farver fra BBR. Den målte model viser tagfladen direkte fra højdemodellen.";

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
renderer.domElement.addEventListener("click", e => {
  const r = renderer.domElement.getBoundingClientRect();
  mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(mouse, camera);
  const hit = ray.intersectObjects(Object.values(groups).filter(g => g.visible), true)
    .find(h => h.object.userData && h.object.userData.nr !== undefined);
  if (!hit) { info.textContent = "Klik på en bygning."; return; }
  const d = hit.object.userData;
  info.innerHTML = `<b>Bygning ${d.nr}</b><table>` + Object.entries(LABELS)
    .filter(([k]) => d[k] !== undefined && d[k] !== null)
    .map(([k, t]) => `<tr><td>${t}</td><td>${d[k]}</td></tr>`).join("") + "</table>";
});

resize();
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
</script>
</body>
</html>
"""
