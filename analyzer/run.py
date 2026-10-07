"""Run the pipeline for one property and save everything as a run under runs/.

Example:
    python -m analyzer.run --bfe 5522814 --center 558376,6369071 --size 400
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import traceback
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from rasterio.features import geometry_mask
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform, unary_union

from . import VERSION, classify, outputs
from . import buildings as bldg
from .classes import BYGNING, NAMES, UDENFOR
from .grid import Grid
from .sources import SourceUnavailable, dataforsyningen, datafordeler, open_rasters, osm, punktsky, skraafoto

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


SECRET_VARS = ("DATAFORDELER_API_KEY", "DATAFORSYNINGEN_TOKEN", "CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET")


def redact(text: str) -> str:
    """Remove secrets from error messages before they are written to run.json (URLs can carry keys)."""
    for var in SECRET_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            text = text.replace(value, f"<{var}>")
    return text


class Sources:
    """Collects status for every source so run.json and the viewer show what was used."""

    def __init__(self):
        self.items = []

    def fetch(self, name: str, fn, *args, **kwargs):
        try:
            value = fn(*args, **kwargs)
            self.items.append({"navn": name, "status": "ok"})
            print(f"  ok   {name}")
            return value
        except SourceUnavailable as e:
            self.items.append({"navn": name, "status": "sprunget over", "note": redact(str(e))})
        except Exception as e:  # keep the run going, but record the failure
            self.items.append({"navn": name, "status": "fejl", "note": redact(f"{type(e).__name__}: {e}")})
            traceback.print_exc()
        print(f"  --   {name}: {self.items[-1]['note']}")
        return None

    def note(self, name: str, status: str, note: str = "", **extra):
        self.items.append({"navn": name, "status": status, "note": note, **extra})


def load_area(path: Path):
    """Read a property polygon from GeoJSON. Coordinates in degrees are reprojected to EPSG:25832."""
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data.get("features") or [data]
    geom = unary_union([shape(f.get("geometry", f)) for f in feats])
    if abs(geom.bounds[0]) <= 180 and abs(geom.bounds[1]) <= 90:
        to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True).transform
        geom = shapely_transform(to_utm, geom)
    return geom


def log_issue(run_name: str, source: str, text: str) -> None:
    """Append an operational problem with a data source to runs/DRIFTSLOG.md."""
    path = RUNS / "DRIFTSLOG.md"
    if not path.exists():
        path.write_text("# Driftslog\n\nProblemer med datakilder under runs. Skrives automatisk af pipelinen.\n\n"
                        "| Tidspunkt | Run | Kilde | Problem |\n|---|---|---|---|\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"| {dt.datetime.now():%Y-%m-%d %H:%M} | {run_name} | {source} | {redact(text)} |\n")


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bfe", required=True, help="BFE-nummer (primær nøgle)")
    ap.add_argument("--omraade", help="GeoJSON-fil med ejendommens polygon, når MAT ikke kan bruges "
                                      "(EPSG:4326 eller EPSG:25832)")
    ap.add_argument("--uden-matrikel", action="store_true",
                    help="tillad en firkant omkring --center uden matrikel. Kun forhåndsvisning: arealerne er ikke "
                         "ejendommens")
    ap.add_argument("--center", help="x,y i EPSG:25832. Kun sammen med --uden-matrikel")
    ap.add_argument("--size", type=float, default=200, help="sidelængde i meter for firkanten omkring --center")
    ap.add_argument("--orto-years", default="2025,2024,2023", help="ortofoto-årgange, nyeste først")
    ap.add_argument("--s2-years", default="2025,2024", help="Sentinel-2 somre")
    ap.add_argument("--uden-punktsky", action="store_true", help="spring punktskyen over (stor download)")
    ap.add_argument("--note", default="", help="fritekst om hvad denne run afprøver")
    args = ap.parse_args()

    started = dt.datetime.now()
    run_name = f"{started:%Y-%m-%d_%H%M}_bfe-{args.bfe}"
    run_dir = RUNS / run_name
    print(f"Run {run_name}")

    src = Sources()
    has_token = bool(os.environ.get("DATAFORSYNINGEN_TOKEN", "").strip())
    res = 0.25 if has_token else 1.0

    # 1. Analysis area: the property from MAT, or a polygon file. Areas are only valid inside the property.
    if args.omraade:
        parcel = load_area(Path(args.omraade))
        src.note(f"Ejendomspolygon fra fil {args.omraade}", "ok")
        area_note = f"polygon fra {args.omraade}"
    else:
        parcel = src.fetch(f"Datafordeler MAT: samlet fast ejendom {args.bfe}", datafordeler.property_area,
                           args.bfe)
        area_note = "matrikel (samlet fast ejendom)"
    if parcel is not None:
        grid = Grid.from_bounds(parcel.bounds, res)
        valid_area = True
    elif args.uden_matrikel and args.center:
        x, y = (float(v) for v in args.center.split(","))
        grid = Grid.around(x, y, args.size, res)
        area_note = f"FORHÅNDSVISNING: firkant {args.size:.0f}x{args.size:.0f} m, ikke afgrænset til matriklen"
        valid_area = False
    else:
        raise SystemExit("Ejendommen kunne ikke afgrænses. Sæt DATAFORDELER_API_KEY og DATAFORDELER_MAT_VERSION "
                         "i .env, eller giv polygonen med --omraade. (--uden-matrikel --center x,y giver kun en "
                         "forhåndsvisning uden gyldige arealer.)")
    (run_dir / "output" / "lag").mkdir(parents=True, exist_ok=True)
    (run_dir / "data").mkdir(exist_ok=True)
    print(f"  område: {area_note}, {grid.width}x{grid.height} px à {res} m")
    if parcel is not None:
        print(f"  ejendommens areal (polygon): {parcel.area:,.0f} m²")

    # 2. Data
    layers = []
    osm_index = len(src.items)
    osm_data = src.fetch("OpenStreetMap (Overpass)", osm.osm, grid)
    osm_item = src.items[osm_index]
    if osm_data:
        info = osm_data["hentning"]
        osm_item["hentning"] = info
        if info["kilde"] != "live":
            osm_item["note"] = f"fra {info['kilde']} ({info['alder_dage']} dage gammel)"
        if info["forsøg"]:
            fails = len(info["forsøg"])
            log_issue(run_name, "Overpass", f"{fails} mislykkede forsøg før svar ({info['kilde']}): "
                      + ", ".join(f"{x['server'].split('/')[2]} {x['resultat']}" for x in info["forsøg"]))
    else:
        log_issue(run_name, "Overpass", osm_item.get("note", "ukendt fejl"))
    gdk_index = len(src.items)
    gdk = src.fetch("GeoDanmark vektor (Datafordeler WFS)", datafordeler.geodanmark, grid)
    if gdk:
        src.items[gdk_index]["objekter"] = gdk["antal"]
        if gdk["mangler"]:
            src.items[gdk_index]["note"] = "ikke fundet i WFS: " + ", ".join(gdk["mangler"])
    # Building mask: GeoDanmark footprints are the authoritative source, OSM is the fallback
    if gdk is not None:
        buildings = gdk["klasser"] == BYGNING
    elif osm_data:
        buildings = osm_data["klasser"] == BYGNING
    else:
        buildings = None

    method, classes, background, params = None, None, None, None
    hover = {}
    skraa_images = []
    dsm = dtm = lidar = None
    if has_token:
        ortho_years = [int(y) for y in args.orto_years.split(",")]
        for year in ortho_years:
            rgb = src.fetch(f"Ortofoto {year} RGB (Dataforsyningen)", dataforsyningen.orthophoto, grid, year)
            if rgb is not None:
                outputs.rgb_png(run_dir / "output" / "lag" / f"orto_{year}.png", rgb)
                layers.append({"navn": f"Ortofoto {year}", "fil": f"output/lag/orto_{year}.png", "type": "base"})
                background = rgb if background is None else background
        cir, cir_year = None, None
        for year in ortho_years:
            cir = src.fetch(f"Ortofoto {year} CIR/nær-infrarød (Dataforsyningen)", dataforsyningen.orthophoto,
                            grid, year, cir=True)
            if cir is not None:
                cir_year = year
                outputs.rgb_png(run_dir / "output" / "lag" / f"orto_{year}_cir.png", cir)
                layers.append({"navn": f"Ortofoto {year} CIR", "fil": f"output/lag/orto_{year}_cir.png",
                               "type": "base", "info": outputs.LAYER_INFO["cir"]})
                break
        dtm = src.fetch("DHM terræn (Dataforsyningen)", dataforsyningen.dhm, grid, "dhm_terraen")
        dsm = src.fetch("DHM overflade (Dataforsyningen)", dataforsyningen.dhm, grid, "dhm_overflade")
        if cir is not None and dtm is not None and dsm is not None:
            method = "ortho_dhm"
            params = classify.DEFAULTS[method]
            ndvi = classify.ndvi_from_cir(cir)
            ndsm = dsm - dtm
            outputs.write_geotiff(run_dir / "data" / "ndsm.tif", grid, ndsm)
            outputs.ndvi_png(run_dir / "output" / "lag" / "ndvi.png", ndvi)
            outputs.height_png(run_dir / "output" / "lag" / "hoejde.png", ndsm)
            layers += [{"navn": f"NDVI (ortofoto {cir_year})", "fil": "output/lag/ndvi.png", "type": "overlay",
                        "legende": outputs.ndvi_legend(params["ndvi_vegetation"])},
                       {"navn": "Højde over terræn (DHM)", "fil": "output/lag/hoejde.png", "type": "overlay",
                        "legende": outputs.height_legend(params)}]
            hover = {"hoejde": ndsm, "ndvi": ndvi}
            lidar = None
            if not args.uden_punktsky:
                pc_index = len(src.items)
                lidar = src.fetch("DHM punktsky 2023 (Datafordeler)", punktsky.punktsky, grid)
                if lidar:
                    src.items[pc_index].update({k: lidar[k] for k in ("punkter", "tæthed_pr_m2", "filer",
                                                                       "fordeling_pct")})
                    outputs.lidar_png(run_dir / "output" / "lag" / "punktsky.png", lidar["antal"])
                    layers.append({"navn": "Punktsky: klasser (DHM 2023)", "fil": "output/lag/punktsky.png",
                                   "type": "overlay", "info": outputs.LAYER_INFO["punktsky"]})
            classes = classify.ortho_dhm(ndvi, ndsm, buildings, params, lidar["antal"] if lidar else None)
            if parcel is not None:
                ground_z = float(np.nanmedian(dtm[~geometry_mask([parcel], out_shape=grid.shape,
                                                                   transform=grid.transform)]))
                sk_index = len(src.items)
                sk = src.fetch("Skråfotos 2025 (Dataforsyningen)", skraafoto.skraafoto, parcel, ground_z,
                               run_dir / "output" / "skraafoto")
                if sk:
                    skraa_images = sk["billeder"]
                    src.items[sk_index]["billeder"] = [{k: b[k] for k in ("retning", "id", "dato")}
                                                       for b in skraa_images]
    else:
        src.note("Dataforsyningen (ortofoto + DHM)", "sprunget over",
                 "DATAFORSYNINGEN_TOKEN mangler i .env. Hovedkilderne er derfor ikke med")

    s2_index = len(src.items)
    s2 = src.fetch("Sentinel-2 L2A (Earth Search, AWS)", open_rasters.sentinel2, grid,
                   [int(y) for y in args.s2_years.split(",")])
    wc = src.fetch("ESA WorldCover 2021 (10 m)", open_rasters.worldcover, grid)
    if not os.environ.get("CDSE_CLIENT_ID", "").strip():
        src.note("Copernicus Data Space (øvrige lag)", "sprunget over",
                 "CDSE_CLIENT_ID/SECRET mangler i .env. Kun Sentinel-2 hentet via Earth Search")
    if s2:
        src.items[s2_index]["scener"] = s2["scener"]
        outputs.rgb_png(run_dir / "output" / "lag" / "sentinel2.png", s2["rgb"])
        outputs.ndvi_png(run_dir / "output" / "lag" / "sentinel2_ndvi.png", s2["ndvi"])
        layers.append({"navn": "Sentinel-2 sommer (median)", "fil": "output/lag/sentinel2.png", "type": "base",
                       "info": outputs.LAYER_INFO["sentinel2"]})
        layers.append({"navn": "Sentinel-2 NDVI", "fil": "output/lag/sentinel2_ndvi.png", "type": "overlay",
                       "legende": outputs.ndvi_legend(classify.DEFAULTS["sentinel2"]["ndvi_vegetation"])})
        outputs.write_geotiff(run_dir / "data" / "sentinel2_ndvi.tif", grid, s2["ndvi"].astype("float32"))
        if background is None:
            background = s2["rgb"]
    if wc is not None:
        wc_rgb = np.zeros((*wc.shape, 4), dtype="uint8")
        for code, color in {10: (16, 82, 32), 20: (46, 139, 87), 30: (139, 205, 80), 40: (230, 220, 110),
                            50: (200, 70, 60), 60: (180, 170, 150), 80: (60, 120, 220)}.items():
            wc_rgb[wc == code] = (*color, 255)
        Image.fromarray(wc_rgb).save(run_dir / "output" / "lag" / "worldcover.png")
        layers.append({"navn": "ESA WorldCover", "fil": "output/lag/worldcover.png", "type": "overlay",
                       "info": outputs.LAYER_INFO["worldcover"]})

    if method is None:
        if s2 is None:
            raise SystemExit("Ingen brugbare billeddata. Se kilderne ovenfor.")
        method = "sentinel2"
        params = classify.DEFAULTS[method]
        classes = classify.sentinel2(s2["ndvi"], wc, buildings, params)

    # 3. Restrict to the analysis area
    if parcel is not None:
        outside = geometry_mask([parcel], out_shape=grid.shape, transform=grid.transform)
        classes[outside] = UDENFOR

    # 4. Outputs
    out = run_dir / "output"
    outputs.write_geotiff(out / "klasser.tif", grid, classes)
    area = outputs.areas(classes, grid)
    outputs.write_areas_csv(out / "arealer.csv", area)
    n_polygons = outputs.write_polygons(out / "arealer.geojson", classes, grid)
    outputs.class_png(out / "lag" / "klasser.png", classes)
    layers.append({"navn": "Resultat: arealtyper", "fil": "output/lag/klasser.png", "type": "overlay", "vis": True,
                   "info": outputs.LAYER_INFO["klasser"]})
    if gdk:
        outputs.class_png(out / "lag" / "geodanmark.png", gdk["klasser"])
        (out / "geodanmark.geojson").write_text(json.dumps(gdk["geojson"]), encoding="utf-8")
        layers.append({"navn": "GeoDanmark (bygninger, træer, p-pladser)", "fil": "output/lag/geodanmark.png",
                       "type": "overlay", "info": outputs.LAYER_INFO["geodanmark"]})
    if osm_data:
        outputs.class_png(out / "lag" / "osm.png", osm_data["klasser"])
        (out / "osm.geojson").write_text(json.dumps(osm_data["geojson"]), encoding="utf-8")
        layers.append({"navn": "OpenStreetMap (til kontrol)", "fil": "output/lag/osm.png", "type": "overlay",
                       "info": outputs.LAYER_INFO["osm"]})
    if parcel is not None:
        outputs.outline_png(out / "lag" / "ejendom.png", parcel, grid)
        layers.append({"navn": "Ejendomsgrænse", "fil": "output/lag/ejendom.png", "type": "overlay", "vis": True,
                       "fast": True, "info": outputs.LAYER_INFO["ejendom"]})
    if background is not None:
        outputs.blend_png(out / "kort.png", background, classes)

    # 5. Buildings on the property: BBR attributes and roof shape from the surface model
    bld_records, bld_summary = [], None
    if gdk and gdk["bygninger"]:
        bbr_index = len(src.items)
        bbr = src.fetch("BBR bygninger (Datafordeler GraphQL)", datafordeler.bbr_buildings,
                        [b["properties"]["BBRUUID"] for b in gdk["bygninger"] if b["properties"].get("BBRUUID")])
        bld_records = bldg.analyse(gdk["bygninger"], bbr, grid, parcel, dsm, dtm,
                                   lidar["antal"] if lidar else None)
        if bld_records:
            bld_summary = bldg.summary(bld_records)
            if bbr is not None:
                src.items[bbr_index]["note"] = (f"{bld_summary['med_bbr']} af {bld_summary['antal']} bygninger på "
                                                "ejendommen har BBR-data")
            bldg.write_csv(out / "bygninger.csv", bld_records)
            bldg.write_geojson(out / "bygninger.geojson", bld_records)
            bld_index = bldg.index_raster(bld_records, grid)
            bldg.roof_png(out / "lag" / "tagmateriale.png", bld_records, bld_index)
            layers.append({"navn": "Bygninger: tagmateriale (BBR)", "fil": "output/lag/tagmateriale.png",
                           "type": "overlay", "legende": bldg.roof_legend(bld_records)})
            hover["bygning"] = bld_index

    metrics = {"arealer_m2": area, "polygoner": n_polygons}
    if bld_summary:
        metrics["bygninger"] = bld_summary
    if gdk:
        metrics["overensstemmelse_geodanmark"] = outputs.agreement(classes, gdk["klasser"], "GeoDanmark (ikke facit)")
    if osm_data:
        metrics["overensstemmelse_osm"] = outputs.agreement(classes, osm_data["klasser"], "OpenStreetMap (ikke facit)")
    ref_path = RUNS / "reference" / f"bfe-{args.bfe}.geojson"
    if ref_path.exists():
        metrics["facit"] = outputs.accuracy(classes, outputs.reference_raster(ref_path, grid))
    (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    run_info = {
        "run": run_name, "tidspunkt": started.isoformat(timespec="seconds"),
        "analyzer_version": VERSION, "git_commit": git_commit(), "bfe": args.bfe,
        "omraade": area_note, "gyldigt_areal": valid_area,
        "ejendom_areal_m2": round(parcel.area, 1) if parcel is not None else None, "grid": {"crs": "EPSG:25832", "bbox": grid.bbox, "opløsning_m": res,
                                       "pixels": [grid.width, grid.height]},
        "metode": method, "parametre": params, "bygning_parametre": bldg.DEFAULTS, "skraafoto": skraa_images, "note": args.note, "kilder": src.items,
        "argumenter": vars(args),
    }
    (run_dir / "run.json").write_text(json.dumps(run_info, ensure_ascii=False, indent=2), encoding="utf-8")
    outputs.write_viewer(run_dir / "viewer.html", f"Analyzer – BFE {args.bfe}", grid, layers, area, run_info,
                         rasters={**hover, "klasse": classes},
                         buildings=[bldg.public(r) for r in bld_records], building_summary=bld_summary)

    template = (RUNS / "_skabelon" / "evaluering.md").read_text(encoding="utf-8")
    template = (template.replace("<run-mappe, fx 2026-10-08_1430_bfe-5522814>", run_name)
                .replace("<BFE-nummer>", args.bfe))
    if not (run_dir / "evaluering.md").exists():
        (run_dir / "evaluering.md").write_text(template, encoding="utf-8")

    _append_overview(run_name, args.bfe, method, src.items, metrics, valid_area)
    print(f"Færdig: {run_dir.relative_to(ROOT)}/viewer.html")
    if not valid_area:
        print("  ADVARSEL: forhåndsvisning. Arealerne gælder firkanten, ikke ejendommen.")
    for k, v in area.items():
        print(f"  {k:<16}{v:>12,.1f} m²")


def _append_overview(run_name: str, bfe: str, method: str, items: list, metrics: dict, valid_area: bool) -> None:
    path = RUNS / "OVERSIGT.md"
    used = ", ".join(i["navn"].split(" (")[0] for i in items if i["status"] == "ok")
    facit = metrics.get("facit", {}).get("pr_klasse_pct", {})
    cell = lambda name: str(facit.get(name, "")) if facit else ""
    score_src = "F" if facit else ""
    row = (f"| [{run_name}]({run_name}/viewer.html) | {bfe} | {method} v{VERSION} | {used} | "
           f"{cell(NAMES[1])} | {cell(NAMES[2])} | | {metrics['facit']['samlet_pct'] if facit else ''} | "
           f"{score_src} | {'afventer' if valid_area else 'ugyldig: ikke afgrænset til matriklen'} | |\n")
    with path.open("a", encoding="utf-8") as f:
        f.write(row)


if __name__ == "__main__":
    main()
