"""Write run outputs: GeoTIFF, CSV, GeoJSON, PNG layers, metrics and the local viewer."""

import base64
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


NDVI_INFO = ("NDVI = Normalized Difference Vegetation Index (normaliseret vegetationsindeks). "
             "Beregnes som (nær-infrarød − rød) / (nær-infrarød + rød). Levende planter reflekterer meget "
             "nær-infrarødt lys og optager rødt lys, så de får høje værdier. Belægning, tage og vand ligger "
             "omkring 0 eller under. Skala fra −1 til 1.")
HEIGHT_INFO = ("Højde over terræn = Danmarks Højdemodel, overflade minus terræn. Viser hvor højt det, der står på "
               "jorden, er: græs og belægning ca. 0 m, buske og hække 0,5–3 m, træer og huse højere.")
LAYER_INFO = {
    "cir": "CIR = farve-infrarødt foto. Nær-infrarødt vises som rødt, så levende vegetation står kraftigt rødt.",
    "klasser": "Run'ens resultat: hver pixel er klassificeret som belægning, græs, buske, træer eller bygning.",
    "geodanmark": "GeoDanmark: officielle kortdata (bygningsomrids, enkelttræer, p-pladser). Bruges som maske og kontrol.",
    "osm": "OpenStreetMap: frivilligt kortlagte data. Kun til kontrol, ikke facit.",
    "worldcover": "ESA WorldCover: europæisk arealdækkekort i 10 m opløsning. Grov baseline.",
    "sentinel2": "Sentinel-2: satellitbilleder fra EU's Copernicus, 10 m opløsning. Median af skyfrie sommerscener.",
    "punktsky": ("Danmarks Højdemodels punktsky (laserscanning 2023). Viser den klasse, de fleste laserpunkter i "
                 "området har: mørkegrøn = høj vegetation (træer), mellemgrøn = mellem vegetation (buske), "
                 "lysegrøn = lav vegetation, rød = bygning, grå = terræn. Bruges til at afgøre træer og buske."),
    "ejendom": "Ejendomsgrænsen fra matriklen (samlet fast ejendom). Arealer regnes kun indenfor.",
}


NDVI_MIN, NDVI_MAX = -0.2, 0.7
HEIGHT_MAX_M = 15.0


def _ndvi_rgb(t: np.ndarray) -> np.ndarray:
    low, high = np.array([150, 110, 70]), np.array([20, 140, 40])
    return low * (1 - t) + high * t


def _height_rgb(t: np.ndarray) -> np.ndarray:
    return np.stack([255 * t, 255 * np.sqrt(t), 255 * (1 - t)], axis=-1)


def _gradient(color_fn, steps: int = 12) -> str:
    stops = []
    for i in range(steps + 1):
        t = i / steps
        r, g, b = (int(v) for v in np.asarray(color_fn(np.array([t]))).reshape(-1)[:3])
        stops.append(f"rgb({r},{g},{b}) {100 * t:.0f}%")
    return "linear-gradient(to right, " + ", ".join(stops) + ")"


def ndvi_png(path: Path, ndvi: np.ndarray) -> None:
    t = np.clip((np.nan_to_num(ndvi, nan=-1) - NDVI_MIN) / (NDVI_MAX - NDVI_MIN), 0, 1)[..., None]
    rgb = _ndvi_rgb(t).astype("uint8")
    alpha = np.where(np.isnan(ndvi), 0, 255).astype("uint8")[..., None]
    Image.fromarray(np.concatenate([rgb, alpha], axis=-1)).save(path, optimize=True)


def ndvi_legend(threshold: float | None = None) -> dict:
    note = "Under 0 er typisk belægning, tag og vand. Over ca. 0,2 er typisk vegetation."
    if threshold is not None:
        note += f" Run'en regner over {threshold:.2f} som vegetation."
    return {"titel": "Vegetationsindeks (NDVI)", "min": NDVI_MIN, "max": NDVI_MAX, "enhed": "",
            "gradient": _gradient(lambda t: _ndvi_rgb(t[:, None])), "ticks": [-0.2, 0, 0.2, 0.4, 0.7],
            "note": note, "info": NDVI_INFO}


def height_png(path: Path, ndsm: np.ndarray) -> None:
    t = np.clip(np.nan_to_num(ndsm, nan=0) / HEIGHT_MAX_M, 0, 1)
    rgb = _height_rgb(t).astype("uint8")
    alpha = np.where(np.isnan(ndsm), 0, 255).astype("uint8")[..., None]
    Image.fromarray(np.concatenate([rgb, alpha], axis=-1)).save(path, optimize=True)


def height_legend(params: dict) -> dict:
    busk, trae = params["hoejde_busk_m"], params["hoejde_trae_m"]
    return {"titel": "Højde over terræn", "min": 0, "max": HEIGHT_MAX_M, "enhed": "m",
            "gradient": _gradient(_height_rgb), "ticks": [0, busk, trae, 6, 9, 12, HEIGHT_MAX_M],
            "note": f"Græs under {busk:g} m, buske {busk:g}–{trae:g} m, træer over {trae:g} m. "
                    f"Alt over {HEIGHT_MAX_M:g} m har samme farve. Hold musen over kortet for præcis højde.",
            "info": HEIGHT_INFO}


def encode_raster(data: np.ndarray, kind: str) -> str:
    """Pack a raster as base64 uint8 for the viewer's hover readout. 255 = no data."""
    if kind == "hoejde":  # decimetres, 0–25.4 m
        v = np.clip(np.round(np.nan_to_num(data, nan=-1) * 10), 0, 254)
        v[np.isnan(data)] = 255
    elif kind == "ndvi":  # -1..1 mapped to 0..254
        v = np.clip(np.round((np.nan_to_num(data, nan=0) + 1) * 127), 0, 254)
        v[np.isnan(data)] = 255
    else:  # class codes
        v = data
    return base64.b64encode(v.astype("uint8").tobytes()).decode("ascii")


LIDAR_COLORS = {2: (170, 170, 170), 3: (170, 220, 120), 4: (60, 160, 80), 5: (16, 82, 32), 6: (200, 70, 60),
                9: (60, 120, 220)}


def lidar_png(path: Path, counts: dict) -> None:
    """Dominant point class per cell, smoothed over a 5x5 neighbourhood."""
    from scipy.ndimage import uniform_filter
    classes = list(counts)
    stack = np.stack([uniform_filter(counts[c].astype("float32"), size=5) for c in classes])
    dominant = np.array(classes)[stack.argmax(axis=0)]
    rgba = np.zeros((*dominant.shape, 4), dtype="uint8")
    for c, color in LIDAR_COLORS.items():
        rgba[dominant == c] = (*color, 255)
    rgba[stack.sum(axis=0) == 0] = 0
    Image.fromarray(rgba).save(path, optimize=True)


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

def write_viewer(path: Path, title: str, grid: Grid, layers: list[dict], area: dict, run_info: dict,
                 rasters: dict | None = None, buildings: list | None = None,
                 building_summary: dict | None = None) -> None:
    legend = [{"navn": NAMES[c], "farve": "#%02x%02x%02x" % COLORS[c]} for c in NAMES]
    html = VIEWER_TEMPLATE.replace("__TITLE__", title).replace("__DATA__", json.dumps({
        "width": grid.width, "height": grid.height, "res": grid.res,
        "layers": layers, "areas": area, "legend": legend, "run": run_info,
        "rasters": {k: encode_raster(v, k) for k, v in (rasters or {}).items()},
        "buildings": buildings or [], "buildingSummary": building_summary,
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
  .i { display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px; margin-left:6px;
       border-radius:50%; border:1px solid var(--muted); color:var(--muted); font:italic 600 10px/1 Georgia, serif;
       vertical-align:1px; cursor:help; }
  .legend { margin:10px 0 6px; }
  .gallery { display:grid; grid-template-columns: 1fr 1fr; gap:6px; }
  .gallery a { display:block; text-decoration:none; color:var(--muted); font-size:11px; }
  .gallery img { width:100%; height:90px; object-fit:cover; border-radius:4px; border:1px solid var(--line); } .legend b { font-size:12px; font-weight:600; }
  .bar { height:12px; border-radius:3px; margin:4px 0 2px; border:1px solid var(--line); }
  .ticks { position:relative; height:14px; font-size:11px; color:var(--muted); font-variant-numeric: tabular-nums; }
  .ticks span { position:absolute; transform:translateX(-50%); white-space:nowrap; }
  .ticks span:first-child { transform:none; } .ticks span:last-child { transform:translateX(-100%); }
  #readout { position:absolute; right:12px; bottom:12px; z-index:1000; background:rgba(20,20,18,.85); color:#fff;
             padding:6px 10px; border-radius:6px; font:12px/1.4 system-ui, sans-serif; pointer-events:none;
             font-variant-numeric: tabular-nums; }
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
  <div id="legends"></div>
  <label class="muted">Gennemsigtighed for lag</label>
  <input type="range" id="opacity" min="0" max="100" value="60">
  <h2>Arealer</h2><table id="areas"></table>
  <div id="bld-sec" hidden><h2>Bygninger og tage</h2><div class="muted" id="bld-meta"></div>
  <table id="bld-mat"></table><table id="bld-form" style="margin-top:8px"></table>
  <div class="muted" style="margin-top:6px">Alle bygninger: <code>output/bygninger.csv</code>. Hold musen over en
  bygning med laget "tagmateriale" tændt.</div></div>
  <div id="skraa-sec" hidden><h2>Skråfotos</h2><div id="skraa" class="gallery"></div>
  <div class="muted">Magenta linje = ejendomsgrænsen. Klik for stort billede.</div></div>
  <h2>Kilder</h2><ul id="sources" class="muted"></ul>
  <h2>Evaluering</h2>
  <div class="muted">Udfyld <code>evaluering.md</code> i run-mappen.</div>
</aside>
<div id="map" style="position:relative"><div id="readout" hidden></div></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const D = __DATA__;
const bounds = [[0, 0], [D.height, D.width]];
const map = L.map('map', { crs: L.CRS.Simple, minZoom: -4, maxZoom: 4, zoomSnap: 0.25 });
map.fitBounds(bounds);
let opacity = 0.6;
const overlays = [];
let base = null;
const legendLayers = [];
const basesEl = document.getElementById('bases'), ovEl = document.getElementById('overlays');
D.layers.forEach((l, i) => {
  const layer = L.imageOverlay(l.fil, bounds, { opacity: (l.type === 'base' || l.fast) ? 1 : opacity });
  const lab = document.createElement('label');
  const inp = document.createElement('input');
  const info = l.info || (l.legende && l.legende.info);
  const name = document.createElement('span'); name.textContent = l.navn;
  if (info) { lab.title = info; name.append(Object.assign(document.createElement('span'),
    { className: 'i', textContent: 'i' })); }
  if (l.type === 'base') {
    inp.type = 'radio'; inp.name = 'base';
    if (!base) { inp.checked = true; base = layer; layer.addTo(map); }
    inp.onchange = () => { if (base) map.removeLayer(base); base = layer; layer.addTo(map); layer.bringToBack(); };
    lab.append(inp, name); basesEl.append(lab);
  } else {
    inp.type = 'checkbox'; inp.checked = !!l.vis;
    if (l.vis) layer.addTo(map);
    inp.onchange = () => { inp.checked ? layer.addTo(map) : map.removeLayer(layer); renderLegends(); };
    if (l.legende) legendLayers.push({ l, inp });
    if (!l.fast) overlays.push(layer);
    lab.append(inp, name); ovEl.append(lab);
  }
});
function renderLegends() {
  const el = document.getElementById('legends'); el.innerHTML = '';
  for (const { l, inp } of legendLayers) {
    if (!inp.checked) continue;
    const g = l.legende, span = g.max - g.min, fmt = v => v.toLocaleString('da-DK') + (g.enhed ? ' ' + g.enhed : '');
    const box = document.createElement('div'); box.className = 'legend';
    const t = document.createElement('b'); t.textContent = g.titel;
    if (g.kategorier) {
      box.append(t);
      for (const k of g.kategorier) { const d = document.createElement('div'); d.className = 'muted';
        d.innerHTML = `<span class="sw" style="background:${k.farve}"></span>`; d.append(k.navn); box.append(d); }
      box.append(Object.assign(document.createElement('div'), { className: 'muted', textContent: g.note }));
      el.append(box); continue;
    }
    if (g.info) { t.title = g.info; t.style.cursor = 'help';
      t.append(Object.assign(document.createElement('span'), { className: 'i', textContent: 'i' })); }
    const why = g.info ? Object.assign(document.createElement('div'), { className: 'muted', textContent: g.info }) : null;
    const bar = document.createElement('div'); bar.className = 'bar'; bar.style.background = g.gradient;
    const ticks = document.createElement('div'); ticks.className = 'ticks';
    g.ticks.forEach(v => { const s = document.createElement('span'); s.style.left = (100 * (v - g.min) / span) + '%';
      s.textContent = fmt(v); ticks.append(s); });
    const n = document.createElement('div'); n.className = 'muted'; n.textContent = g.note;
    box.append(t, bar, ticks, n); if (why) box.append(why); el.append(box);
  }
}
renderLegends();
const decode = b => b ? Uint8Array.from(atob(b), c => c.charCodeAt(0)) : null;
const R = { hoejde: decode(D.rasters.hoejde), ndvi: decode(D.rasters.ndvi), klasse: decode(D.rasters.klasse),
            bygning: decode(D.rasters.bygning) };
const bldByNr = Object.fromEntries(D.buildings.map(b => [b.nr, b]));
const num = v => v == null ? '?' : v.toLocaleString('da-DK');
function bldText(b) {
  const p = [`Bygning ${b.nr}`];
  if (b.anvendelse) p.push(b.anvendelse + (b.opfoerelsesaar ? ` (${b.opfoerelsesaar})` : ''));
  p.push('tag: ' + (b.tagmateriale || 'ingen BBR'));
  if (b.tagform) p.push(b.tagform + (b.tagform !== 'fladt' && b.taghaeldning_grader != null ? ` ${num(b.taghaeldning_grader)}°` : ''));
  if (b.tagareal_m2) p.push(`tagareal ${num(b.tagareal_m2)} m²`);
  if (b.hoejde_max_m != null) p.push(`højde ${num(b.hoejde_max_m)} m`);
  if (b.mulig_asbest) p.push('MULIG ASBEST');
  return p.join(' · ');
}
const classNames = Object.fromEntries(D.legend.map((x, i) => [i + 1, x.navn]));
const ro = document.getElementById('readout');
map.on('mousemove', e => {
  const row = Math.floor(D.height - e.latlng.lat), col = Math.floor(e.latlng.lng);
  if (row < 0 || col < 0 || row >= D.height || col >= D.width) { ro.hidden = true; return; }
  const i = row * D.width + col, parts = [];
  if (R.bygning && R.bygning[i] && bldByNr[R.bygning[i]]) parts.push(bldText(bldByNr[R.bygning[i]]));
  else if (R.klasse) parts.push(classNames[R.klasse[i]] || 'Uden for ejendommen');
  if (R.hoejde && R.hoejde[i] !== 255) parts.push('Højde ' + (R.hoejde[i] / 10).toLocaleString('da-DK', { minimumFractionDigits: 1 }) + ' m');
  if (R.ndvi && R.ndvi[i] !== 255) parts.push('NDVI (grønhed) ' + (R.ndvi[i] / 127 - 1).toLocaleString('da-DK', { maximumFractionDigits: 2 }));
  ro.textContent = parts.join(' · '); ro.hidden = parts.length === 0;
});
map.on('mouseout', () => { ro.hidden = true; });
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
if (D.run.skraafoto && D.run.skraafoto.length) {
  document.getElementById('skraa-sec').hidden = false;
  const g = document.getElementById('skraa');
  for (const b of D.run.skraafoto) {
    const a = document.createElement('a'); a.href = 'output/skraafoto/' + b.fil; a.target = '_blank';
    const img = document.createElement('img'); img.src = a.href; img.alt = b.navn; img.loading = 'lazy';
    const cap = document.createElement('div'); cap.textContent = `${b.navn} · ${b.dato}`;
    a.append(img, cap); g.append(a);
  }
}
if (D.buildingSummary) {
  const S = D.buildingSummary;
  document.getElementById('bld-sec').hidden = false;
  document.getElementById('bld-meta').textContent = `${S.antal} bygninger på ejendommen, ${S.med_bbr} med BBR-data`
    + (S.mulig_asbest ? `. ${S.mulig_asbest} med mulig asbest (fibercement før 1986)` : '')
    + '. Tagareal er beregnet ud fra hældningen.';
  const fill = (id, head, groups) => {
    const t = document.getElementById(id);
    t.insertAdjacentHTML('beforeend', `<tr><td><b>${head}</b></td><td class="n"><b>Antal</b></td><td class="n"><b>Tagareal</b></td></tr>`);
    for (const [k, g] of Object.entries(groups)) {
      const tr = document.createElement('tr'), a = document.createElement('td'); a.textContent = k;
      tr.append(a); tr.insertAdjacentHTML('beforeend', `<td class="n">${g.antal}</td><td class="n">${num(g.tagareal_m2)} m²</td>`);
      t.append(tr);
    }
  };
  fill('bld-mat', 'Tagmateriale', S.pr_tagmateriale);
  fill('bld-form', 'Tagform (beregnet)', S.pr_tagform);
}
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
