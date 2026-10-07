"""Write run outputs: GeoTIFF, CSV, GeoJSON, PNG layers, metrics and the local viewer."""

import csv
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.features import rasterize, shapes
from shapely.geometry import mapping, shape
from shapely.ops import transform

from .classes import COLORS, NAMES, OUTDOOR, UDENFOR
from .grid import CRS, Grid

_to_wgs84 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform


def write_geotiff(path: Path, grid: Grid, data: np.ndarray) -> None:
    with rasterio.open(path, "w", driver="GTiff", width=grid.width, height=grid.height, count=1,
                       dtype=data.dtype, crs=CRS, transform=grid.transform, compress="deflate") as ds:
        ds.write(data, 1)


def areas(classes: np.ndarray, grid: Grid) -> dict:
    px = grid.res * grid.res
    result = {NAMES[c]: round(float((classes == c).sum() * px), 1) for c in NAMES}
    outdoor = sum(result[NAMES[c]] for c in OUTDOOR)
    result["Udeareal i alt"] = round(outdoor, 1)
    return result


def write_areas_csv(path: Path, area: dict) -> None:
    total = area["Udeareal i alt"] or 1.0
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["arealtype", "m2", "andel_af_udeareal_pct"])
        for name, m2 in area.items():
            share = "" if name in ("Bygning", "Udeareal i alt") else round(100 * m2 / total, 1)
            w.writerow([name, m2, share])


def write_polygons(path: Path, classes: np.ndarray, grid: Grid) -> int:
    features = []
    for geom, value in shapes(classes, mask=classes != UDENFOR, transform=grid.transform):
        poly = shape(geom).simplify(grid.res, preserve_topology=True)
        if poly.is_empty:
            continue
        features.append({"type": "Feature", "geometry": mapping(transform(_to_wgs84, poly)),
                         "properties": {"klasse": int(value), "navn": NAMES[int(value)],
                                        "areal_m2": round(poly.area, 2)}})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return len(features)


# ---------- PNG layers ----------

def class_png(path: Path, classes: np.ndarray) -> None:
    rgba = np.zeros((*classes.shape, 4), dtype="uint8")
    for c, color in COLORS.items():
        rgba[classes == c] = (*color, 255)
    Image.fromarray(rgba).save(path, optimize=True)


def rgb_png(path: Path, rgb: np.ndarray) -> None:
    Image.fromarray(rgb.astype("uint8")).save(path, optimize=True)


def ndvi_png(path: Path, ndvi: np.ndarray) -> None:
    t = np.clip((np.nan_to_num(ndvi, nan=-1) + 0.2) / 0.9, 0, 1)[..., None]
    low, high = np.array([150, 110, 70]), np.array([20, 140, 40])
    rgb = (low * (1 - t) + high * t).astype("uint8")
    alpha = np.where(np.isnan(ndvi), 0, 255).astype("uint8")[..., None]
    Image.fromarray(np.concatenate([rgb, alpha], axis=-1)).save(path, optimize=True)


def height_png(path: Path, ndsm: np.ndarray, max_m: float = 15.0) -> None:
    t = np.clip(np.nan_to_num(ndsm, nan=0) / max_m, 0, 1)
    rgb = np.stack([255 * t, 255 * np.sqrt(t), 255 * (1 - t)], axis=-1).astype("uint8")
    alpha = np.where(np.isnan(ndsm), 0, 255).astype("uint8")[..., None]
    Image.fromarray(np.concatenate([rgb, alpha], axis=-1)).save(path, optimize=True)


def outline_png(path: Path, geom, grid: Grid, width_m: float = 1.0) -> None:
    ring = geom.boundary.buffer(max(width_m, grid.res * 2) / 2)
    m = rasterize([(ring, 1)], out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8")
    rgba = np.zeros((*grid.shape, 4), dtype="uint8")
    rgba[m == 1] = (255, 0, 200, 255)
    Image.fromarray(rgba).save(path, optimize=True)


def blend_png(path: Path, background: np.ndarray, classes: np.ndarray, alpha: float = 0.45) -> None:
    out = background.astype("float32").copy()
    for c, color in COLORS.items():
        m = classes == c
        out[m] = out[m] * (1 - alpha) + np.array(color) * alpha
    Image.fromarray(out.astype("uint8")).save(path, optimize=True)


# ---------- Metrics ----------

def agreement(classes: np.ndarray, other: np.ndarray, label: str) -> dict:
    """Per-class share of pixels where our class equals the other source's class (where it has one)."""
    result = {"sammenlignet_med": label, "pr_klasse_pct": {}}
    valid = (other != UDENFOR) & (classes != UDENFOR)
    for c, name in NAMES.items():
        m = valid & (other == c)
        if m.sum() > 0:
            result["pr_klasse_pct"][name] = round(100 * float((classes[m] == c).mean()), 1)
    result["samlet_pct"] = round(100 * float((classes[valid] == other[valid]).mean()), 1) if valid.any() else None
    return result


def reference_raster(path: Path, grid: Grid) -> np.ndarray:
    """Rasterize a reference GeoJSON (EPSG:4326, property 'klasse' as number or name)."""
    to_utm = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform
    by_name = {v.lower(): k for k, v in NAMES.items()}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for f in data["features"]:
        k = f["properties"].get("klasse")
        k = by_name.get(str(k).lower()) if not isinstance(k, int) else k
        if k:
            items.append((transform(to_utm, shape(f["geometry"])), k))
    return rasterize(items, out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8")


def accuracy(classes: np.ndarray, ref: np.ndarray) -> dict:
    """Accuracy per reference class and a confusion matrix (rows = facit, columns = run)."""
    valid = (ref != UDENFOR) & (classes != UDENFOR)
    result = agreement(classes, ref, "facit")
    matrix = {}
    for r, rname in NAMES.items():
        m = valid & (ref == r)
        if m.any():
            matrix[rname] = {NAMES[c]: int((classes[m] == c).sum()) for c in NAMES}
    result["fejlmatrix_px"] = matrix
    return result


# ---------- Viewer ----------

def write_viewer(path: Path, title: str, grid: Grid, layers: list[dict], area: dict, run_info: dict) -> None:
    legend = [{"navn": NAMES[c], "farve": "#%02x%02x%02x" % COLORS[c]} for c in NAMES]
    html = VIEWER_TEMPLATE.replace("__TITLE__", title).replace("__DATA__", json.dumps({
        "width": grid.width, "height": grid.height, "res": grid.res,
        "layers": layers, "areas": area, "legend": legend, "run": run_info,
    }, ensure_ascii=False))
    path.write_text(html, encoding="utf-8")


VIEWER_TEMPLATE = """<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
  :root { --bg:#f6f6f4; --panel:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#e2e2dc; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161615; --panel:#20201e; --text:#ececea; --muted:#9a9a94; --line:#33332f; } }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 system-ui, sans-serif; background:var(--bg); color:var(--text);
         display:grid; grid-template-columns: 320px 1fr; height:100vh; }
  aside { background:var(--panel); border-right:1px solid var(--line); padding:16px; overflow:auto; }
  h1 { font-size:16px; margin:0 0 4px; } h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em;
       color:var(--muted); margin:18px 0 8px; }
  .muted { color:var(--muted); font-size:12px; }
  .warn { margin-top:10px; padding:8px 10px; border-radius:6px; background:#b3261e; color:#fff; font-size:13px; }
  label { display:flex; gap:8px; align-items:center; padding:3px 0; cursor:pointer; }
  table { width:100%; border-collapse:collapse; font-variant-numeric: tabular-nums; }
  td { padding:3px 0; border-bottom:1px solid var(--line); } td.n { text-align:right; }
  .sw { display:inline-block; width:12px; height:12px; border-radius:2px; margin-right:6px; vertical-align:-1px; }
  #map { height:100vh; background:#000; }
  ul { padding-left:18px; margin:4px 0; } li { margin:2px 0; }
  input[type=range] { width:100%; }
  @media (max-width: 720px) { body { grid-template-columns: 1fr; grid-template-rows: auto 70vh; height:auto; }
    #map { height:70vh; } }
</style>
</head>
<body>
<aside>
  <h1>__TITLE__</h1>
  <div class="muted" id="meta"></div>
  <div id="warn" class="warn" hidden></div>
  <h2>Baggrund</h2><div id="bases"></div>
  <h2>Lag</h2><div id="overlays"></div>
  <label class="muted">Gennemsigtighed for lag</label>
  <input type="range" id="opacity" min="0" max="100" value="60">
  <h2>Arealer</h2><table id="areas"></table>
  <h2>Kilder</h2><ul id="sources" class="muted"></ul>
  <h2>Evaluering</h2>
  <div class="muted">Udfyld <code>evaluering.md</code> i run-mappen.</div>
</aside>
<div id="map"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const D = __DATA__;
const bounds = [[0, 0], [D.height, D.width]];
const map = L.map('map', { crs: L.CRS.Simple, minZoom: -4, maxZoom: 4, zoomSnap: 0.25 });
map.fitBounds(bounds);
let opacity = 0.6;
const overlays = [];
let base = null;
const basesEl = document.getElementById('bases'), ovEl = document.getElementById('overlays');
D.layers.forEach((l, i) => {
  const layer = L.imageOverlay(l.fil, bounds, { opacity: (l.type === 'base' || l.fast) ? 1 : opacity });
  const lab = document.createElement('label');
  const inp = document.createElement('input');
  if (l.type === 'base') {
    inp.type = 'radio'; inp.name = 'base';
    if (!base) { inp.checked = true; base = layer; layer.addTo(map); }
    inp.onchange = () => { if (base) map.removeLayer(base); base = layer; layer.addTo(map); layer.bringToBack(); };
    lab.append(inp, l.navn); basesEl.append(lab);
  } else {
    inp.type = 'checkbox'; inp.checked = !!l.vis;
    if (l.vis) layer.addTo(map);
    inp.onchange = () => inp.checked ? layer.addTo(map) : map.removeLayer(layer);
    if (!l.fast) overlays.push(layer);
    lab.append(inp, l.navn); ovEl.append(lab);
  }
});
document.getElementById('opacity').oninput = e => { opacity = e.target.value / 100; overlays.forEach(o => o.setOpacity(opacity)); };
const colors = Object.fromEntries(D.legend.map(x => [x.navn, x.farve]));
const tb = document.getElementById('areas');
for (const [k, v] of Object.entries(D.areas)) {
  const sw = colors[k] ? `<span class="sw" style="background:${colors[k]}"></span>` : '';
  tb.insertAdjacentHTML('beforeend', `<tr><td>${sw}${k}</td><td class="n">${v.toLocaleString('da-DK')} m²</td></tr>`);
}
if (D.run.gyldigt_areal === false) {
  const w = document.getElementById('warn'); w.hidden = false;
  w.textContent = 'Forhåndsvisning: området er ikke afgrænset til matriklen. Arealerne gælder ikke ejendommen.';
}
if (D.run.ejendom_areal_m2) D.areas = { 'Ejendom (polygon)': D.run.ejendom_areal_m2, ...D.areas };
document.getElementById('meta').textContent = `${D.run.run} · metode ${D.run.metode} · ${D.res} m/px`;
const src = document.getElementById('sources');
for (const s of D.run.kilder) {
  const li = document.createElement('li');
  li.textContent = `${s.status === 'ok' ? '✓' : '✗'} ${s.navn}${s.note ? ' – ' + s.note : ''}`;
  src.append(li);
}
</script>
</body>
</html>
"""
