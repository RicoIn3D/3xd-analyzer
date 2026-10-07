"""Buildings on the property: BBR attributes (roof material, walls, use, year) and roof shape from DHM.

Roof shape, pitch, roof area and height are estimated from the DHM surface model inside each GeoDanmark
footprint. They are not registered values. Roof material comes from BBR and can be outdated.
When the point cloud is available, only cells with building points are used, so trees over a roof are left out.
"""

import csv
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from rasterio.features import rasterize
from scipy.ndimage import uniform_filter
from shapely.geometry import mapping
from shapely.ops import transform

from .grid import CRS, Grid

_to_wgs84 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform

# BBR code lists (byg033, byg032, byg021). Unknown codes are shown as "kode <n>".
TAGMATERIALE = {
    "1": "Tagpap, fladt (built-up)", "2": "Tagpap med hældning", "3": "Fibercement, herunder asbest",
    "4": "Cementsten", "5": "Tegl", "6": "Metal", "7": "Stråtag", "10": "Fibercement, asbestfri", "11": "PVC",
    "12": "Glas", "20": "Grønt tag", "80": "Ingen", "90": "Andet materiale",
}
YDERVAEG = {
    "1": "Mursten", "2": "Letbeton", "3": "Fibercement, herunder asbest", "4": "Bindingsværk", "5": "Træ",
    "6": "Betonelementer", "8": "Metal", "10": "Fibercement, asbestfri", "11": "PVC", "12": "Glas", "80": "Ingen",
    "90": "Andet materiale",
}
ANVENDELSE = {
    "110": "Stuehus til landbrug", "120": "Fritliggende enfamiliehus", "121": "Sammenbygget enfamiliehus",
    "122": "Enfamiliehus i tæt-lav bebyggelse", "130": "Række-, kæde- eller dobbelthus",
    "131": "Række-, kæde- og klyngehus", "132": "Dobbelthus", "140": "Etagebolig", "150": "Kollegium",
    "190": "Anden helårsbolig", "310": "Transport- og garageanlæg", "313": "Parkerings- og transportanlæg",
    "510": "Sommerhus", "910": "Garage", "920": "Carport", "930": "Udhus", "940": "Drivhus",
    "950": "Fritliggende overdækning", "960": "Fritliggende udestue", "990": "Faldefærdig bygning",
    "999": "Ukendt bygning",
}
ASBEST_CODES = {"3"}
ASBEST_BEFORE_YEAR = 1986  # asbestos in fibre cement was banned in Denmark from 1986

TAG_COLORS = {
    "1": (55, 55, 55), "2": (95, 95, 95), "3": (235, 140, 0), "4": (150, 120, 105), "5": (190, 80, 50),
    "6": (90, 130, 175), "7": (205, 175, 95), "10": (175, 175, 160), "11": (225, 225, 225), "12": (150, 210, 230),
    "20": (80, 160, 60), "80": (255, 255, 255), "90": (170, 90, 200),
}
NO_BBR_COLOR = (240, 200, 40)

DEFAULTS = {
    "min_andel_paa_ejendom": 0.5,   # a building counts when at least this share of its footprint is on the property
    "indre_buffer_m": 1.0,          # roof cells this far inside the footprint edge are used (avoids walls and eaves)
    "glatning_px": 5,               # smoothing window for the surface model before slopes are computed
    "fladt_under_grader": 10.0,     # slope below this is flat
    "fladt_andel": 0.6,             # flat when this share of roof cells is flat
    "sammensat_andel": 0.3,         # partly flat when the flat share is between this and fladt_andel
    "gavl_andel": 0.25,             # hip roof when this share of sloped cells faces the short ends
    "side_andel": 0.2,              # gable roof when both long sides have at least this share
    "buet_spredning_grader": 8.0,   # curved roof when the slopes of sloped cells spread this much (IQR)
    "punktsky_bygning_andel": 0.5,  # a roof cell needs this share of building points (class 6) nearby
    "punktsky_min_daekning": 0.3,   # below this share of usable roof cells the roof is marked as covered
    "terraen_ring_m": (1.0, 4.0),   # ground level is the terrain model in this ring outside the footprint
}
POINT_CLOUD_YEAR = 2023


def _label(table: dict, code) -> str | None:
    if code in (None, ""):
        return None
    return table.get(str(code), f"kode {code}")


def _long_axis_deg(geom) -> float:
    """Compass direction (0–180°) of the footprint's long axis, from the minimum rotated rectangle."""
    rect = geom.minimum_rotated_rectangle
    pts = list(rect.exterior.coords)
    edges = [(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(2)]
    dx, dy = max(edges, key=lambda e: math.hypot(*e))
    return math.degrees(math.atan2(dx, dy)) % 180


def _direction_name(deg: float) -> str:
    names = ("N-S", "NNØ-SSV", "NØ-SV", "ØNØ-VSV", "Ø-V", "ØSØ-VNV", "SØ-NV", "SSØ-NNV")
    return names[round(deg / 22.5) % 8]


def _slopes(dsm: np.ndarray, res: float, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Slope in degrees and downslope compass direction from a NaN-aware smoothed surface model."""
    valid = ~np.isnan(dsm)
    filled = np.where(valid, dsm, 0.0)
    weight = uniform_filter(valid.astype("float32"), size=size)
    smooth = uniform_filter(filled, size=size) / np.maximum(weight, 1e-6)
    smooth[weight < 0.5] = np.nan
    d_row, d_col = np.gradient(smooth, res)
    slope = np.degrees(np.arctan(np.hypot(d_row, d_col)))
    # Rows grow southwards. Downslope: east component = -dz/dx, north component = +dz/drow.
    aspect = np.degrees(np.arctan2(-d_col, d_row)) % 360
    return slope, aspect


def _roof_shape(slope: np.ndarray, aspect: np.ndarray, inner: np.ndarray, axis_deg: float, p: dict) -> dict:
    s, a = slope[inner], aspect[inner]
    ok = ~np.isnan(s)
    s, a = s[ok], a[ok]
    if s.size < 8:
        return {"tagform": "ukendt (for lille)", "taghaeldning_grader": None, "flad_andel": None}
    flat = s < p["fladt_under_grader"]
    flat_share = float(flat.mean())
    sloped = ~flat & (s < 70)
    pitch = float(np.median(s[sloped])) if sloped.any() else float(np.median(s))
    if flat_share >= p["fladt_andel"] or not sloped.any():
        form = "fladt"
        pitch = float(np.median(s))
    else:
        rel = (a[sloped] - axis_deg) % 360
        side_a = float(((rel >= 45) & (rel < 135)).mean())
        side_b = float(((rel >= 225) & (rel < 315)).mean())
        ends = 1.0 - side_a - side_b
        q1, q3 = np.percentile(s[s < 70], [25, 75])
        both_sides = side_a >= p["side_andel"] and side_b >= p["side_andel"]
        if both_sides and flat_share >= p["sammensat_andel"] and q3 - q1 >= p["buet_spredning_grader"]:
            # Flat at the top and steeper towards both eaves, with no single pitch: a curved (barrel) roof
            return {"tagform": "buet (tøndetag)", "taghaeldning_grader": round(pitch, 1),
                    "flad_andel": round(flat_share, 2)}
        if ends >= p["gavl_andel"]:
            form = "valmtag"
        elif both_sides:
            form = "sadeltag"
        else:
            form = "pulttag (ensidigt)"
        if flat_share >= p["sammensat_andel"]:
            form = f"sammensat ({form} + fladt)"
    return {"tagform": form, "taghaeldning_grader": round(pitch, 1), "flad_andel": round(flat_share, 2)}


def analyse(footprints: list[dict], bbr: dict | None, grid: Grid, parcel, dsm: np.ndarray | None,
            dtm: np.ndarray | None, lidar: dict | None = None, p: dict = DEFAULTS) -> list[dict]:
    """One record per building on the property. footprints come from GeoDanmark (EPSG:25832).
    lidar is the point count per class and cell from the point cloud (optional)."""
    slope = aspect = None
    if dsm is not None:
        slope, aspect = _slopes(dsm, grid.res, p["glatning_px"])
    roof_points = None
    if lidar is not None:
        size = p["glatning_px"]
        total = uniform_filter(sum(v.astype("float32") for v in lidar.values()), size=size)
        roof_points = uniform_filter(lidar[6].astype("float32"), size=size) >= p["punktsky_bygning_andel"] * np.maximum(
            total, 1e-6)
        roof_points &= total > 0
    records = []
    for fp in footprints:
        geom, props = fp["geom"], fp["properties"]
        if geom.is_empty or geom.area < 1:
            continue
        if parcel is not None:
            on_parcel = geom.intersection(parcel).area / geom.area
            if on_parcel < p["min_andel_paa_ejendom"]:
                continue
        bbr_id = props.get("BBRUUID")
        b = (bbr or {}).get(bbr_id) if bbr_id else None
        tag_code = b.get("byg033Tagdaekningsmateriale") if b else None
        wall_code = b.get("byg032YdervaeggensMateriale") if b else None
        year = b.get("byg026Opfoerelsesaar") if b else None
        axis = _long_axis_deg(geom)
        rec = {
            "geodanmark_id": props.get("id_lokalId"), "bbr_id": bbr_id,
            "bbr_fundet": b is not None,
            "anvendelse": _label(ANVENDELSE, b.get("byg021BygningensAnvendelse")) if b else None,
            "opfoerelsesaar": year, "ombygningsaar": b.get("byg027OmTilbygningsaar") if b else None,
            "etager": b.get("byg054AntalEtager") if b else None,
            "tagmateriale_kode": tag_code, "tagmateriale": _label(TAGMATERIALE, tag_code),
            "ydervaeg": _label(YDERVAEG, wall_code),
            "mulig_asbest": bool((tag_code in ASBEST_CODES or wall_code in ASBEST_CODES)
                                 and (year is None or year < ASBEST_BEFORE_YEAR)),
            "bebygget_areal_bbr_m2": b.get("byg041BebyggetAreal") if b else None,
            "grundplan_m2": round(geom.area, 1),
            "laengderetning": _direction_name(axis),
            "tagform": None, "taghaeldning_grader": None, "flad_andel": None, "tagareal_m2": None,
            "hoejde_median_m": None, "hoejde_max_m": None, "tag_daekning": None,
        }
        if slope is not None:
            full = rasterize([(geom, 1)], out_shape=grid.shape, transform=grid.transform, fill=0,
                             dtype="uint8").astype(bool)
            inner_geom = geom.buffer(-p["indre_buffer_m"])
            inner = rasterize([(inner_geom, 1)], out_shape=grid.shape, transform=grid.transform, fill=0,
                              dtype="uint8").astype(bool) if not inner_geom.is_empty else full
            if inner.sum() < 8:
                inner = full
            if roof_points is not None:
                # Only cells where the point cloud sees the roof itself. Trees over the roof are left out.
                seen = inner & roof_points
                rec["tag_daekning"] = round(float(seen.sum()) / max(int(inner.sum()), 1), 2)
                inner = seen
            rec.update(_roof_shape(slope, aspect, inner, axis, p))
            if rec["tag_daekning"] is not None and rec["tag_daekning"] < p["punktsky_min_daekning"]:
                why = (f"bygget efter punktskyen ({POINT_CLOUD_YEAR})" if year and year > POINT_CLOUD_YEAR
                       else "tag dækket af træer")
                rec["tagform"] = f"usikker: {why}"
                rec["taghaeldning_grader"] = rec["tagareal_m2"] = None
            pitch = rec["taghaeldning_grader"]
            if pitch is not None:
                flat_share = rec["flad_andel"] if rec["tagform"] != "fladt" else 1.0
                factor = flat_share + (1 - flat_share) / math.cos(math.radians(min(pitch, 60)))
                rec["tagareal_m2"] = round(geom.area * factor, 1)
            if dtm is not None and full.any():
                # The terrain model is interpolated under buildings, so ground level is taken just outside
                near, far = p["terraen_ring_m"]
                ring_geom = geom.buffer(far).difference(geom.buffer(near))
                ring = rasterize([(ring_geom, 1)], out_shape=grid.shape, transform=grid.transform, fill=0,
                                 dtype="uint8").astype(bool) if not ring_geom.is_empty else full
                ground = np.nanmedian(dtm[ring]) if ring.any() else np.nanmedian(dtm[full])
                roof = dsm[inner]
                roof = roof[~np.isnan(roof)]
                if roof.size and not np.isnan(ground):
                    rec["hoejde_median_m"] = round(float(np.median(roof) - ground), 1)
                    rec["hoejde_max_m"] = round(float(np.percentile(roof, 98) - ground), 1)
        rec["_geom"] = geom
        records.append(rec)
    records.sort(key=lambda r: -r["grundplan_m2"])
    for i, r in enumerate(records, start=1):
        r["nr"] = i
    return records


def summary(records: list[dict]) -> dict:
    """Totals per roof material and per roof shape."""
    def group(key):
        out = {}
        for r in records:
            k = r[key] or ("ingen BBR-kobling" if key == "tagmateriale" else "ukendt")
            g = out.setdefault(k, {"antal": 0, "grundplan_m2": 0.0, "tagareal_m2": 0.0})
            g["antal"] += 1
            g["grundplan_m2"] = round(g["grundplan_m2"] + r["grundplan_m2"], 1)
            g["tagareal_m2"] = round(g["tagareal_m2"] + (r["tagareal_m2"] or r["grundplan_m2"]), 1)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["tagareal_m2"]))
    return {"antal": len(records), "med_bbr": sum(r["bbr_fundet"] for r in records),
            "mulig_asbest": sum(r["mulig_asbest"] for r in records),
            "pr_tagmateriale": group("tagmateriale"), "pr_tagform": group("tagform")}


FIELDS = ("nr", "anvendelse", "opfoerelsesaar", "ombygningsaar", "etager", "tagmateriale", "ydervaeg",
          "mulig_asbest", "tagform", "taghaeldning_grader", "laengderetning", "grundplan_m2",
          "bebygget_areal_bbr_m2", "tagareal_m2", "hoejde_median_m", "hoejde_max_m", "flad_andel",
          "tag_daekning", "tagmateriale_kode", "bbr_fundet", "bbr_id", "geodanmark_id")


def public(r: dict) -> dict:
    return {k: r[k] for k in FIELDS}


def write_csv(path: Path, records: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
        w.writeheader()
        for r in records:
            w.writerow(public(r))


def write_geojson(path: Path, records: list[dict]) -> None:
    features = [{"type": "Feature", "geometry": mapping(transform(_to_wgs84, r["_geom"])), "properties": public(r)}
                for r in records]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
                    encoding="utf-8")


def index_raster(records: list[dict], grid: Grid) -> np.ndarray:
    """Building number per cell (0 = none), for the viewer's hover readout. Max 254 buildings."""
    items = [(r["_geom"], r["nr"]) for r in records if r["nr"] < 255]
    if not items:
        return np.zeros(grid.shape, dtype="uint8")
    return rasterize(items, out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8")


def roof_png(path: Path, records: list[dict], index: np.ndarray) -> None:
    rgba = np.zeros((*index.shape, 4), dtype="uint8")
    for r in records:
        color = TAG_COLORS.get(str(r["tagmateriale_kode"]), NO_BBR_COLOR) if r["bbr_fundet"] else NO_BBR_COLOR
        rgba[index == r["nr"]] = (*color, 255)
    Image.fromarray(rgba).save(path, optimize=True)


def roof_legend(records: list[dict]) -> dict:
    seen = {}
    for r in records:
        if r["bbr_fundet"]:
            seen[r["tagmateriale"]] = TAG_COLORS.get(str(r["tagmateriale_kode"]), NO_BBR_COLOR)
        else:
            seen["Ingen BBR-kobling"] = NO_BBR_COLOR
    return {"titel": "Tagmateriale (BBR)",
            "kategorier": [{"navn": k, "farve": "#%02x%02x%02x" % c} for k, c in seen.items()],
            "note": "Fra BBR. Kan være forældet. Hold musen over en bygning for detaljer.",
            "info": ("Tagmateriale, ydervæg, anvendelse og opførelsesår er registreret i BBR. Tagform, hældning, "
                     "tagareal og højde er beregnet af Analyzer ud fra Danmarks Højdemodel (overflade).")}
