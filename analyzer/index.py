"""Front page for runs/: one row per property (BFE) with its latest run, older runs folded out.

Written to runs/index.html after every run. Can also be rebuilt on its own: python -m analyzer.index
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
FALLBACK = {"sentinel2": "Sentinel-2-fallback (Dataforsyningen ikke tilgængelig)"}


def _runs(runs_dir: Path) -> list[dict]:
    out = []
    for path in sorted(runs_dir.glob("*/run.json")):
        if path.parent.name.startswith("_") or path.parent.name == "reference":
            continue
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        metrics_path = path.parent / "metrics.json"
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        except ValueError:
            metrics = {}
        model = info.get("model") or {}
        out.append({
            "mappe": path.parent.name, "bfe": str(info.get("bfe") or "?"), "tid": info.get("tidspunkt") or "",
            "version": info.get("analyzer_version") or "", "metode": info.get("metode") or "",
            "note": info.get("note") or "", "gyldig": info.get("gyldigt_areal", True),
            "areal": info.get("ejendom_areal_m2"), "arealer": metrics.get("arealer_m2") or {},
            "model": bool(model.get("filer")) and (path.parent / "output" / "model" / "model.html").exists(),
            "bygninger": model.get("bygninger"),
        })
    return out


def _warning(run: dict) -> str:
    if not run["gyldig"]:
        return "Forhåndsvisning, ikke afgrænset til matriklen"
    return FALLBACK.get(run["metode"], "")


def _links(run: dict) -> str:
    folder = html.escape(run["mappe"])
    links = [f'<a href="{folder}/viewer.html">Kort</a>']
    if run["model"]:
        links.append(f'<a href="{folder}/output/model/model.html">3D</a>')
    return " · ".join(links)


def _row(run: dict, latest: bool) -> str:
    when = html.escape(run["tid"].replace("T", " ")[:16])
    warn = _warning(run)
    areas = run["arealer"]
    summary = ", ".join(f"{k} {v:,.0f} m²".replace(",", ".") for k, v in areas.items()
                        if k in ("Belægning", "Græs", "Buske", "Træer") and isinstance(v, (int, float)))
    cells = [
        f'<td class="bfe">{html.escape(run["bfe"]) if latest else ""}</td>',
        f"<td>{when}</td>",
        f'<td>{_links(run)}</td>',
        f'<td class="muted">{html.escape(run["metode"])} v{html.escape(run["version"])}</td>',
        f'<td>{html.escape(summary)}{"<div class=warn>" + html.escape(warn) + "</div>" if warn else ""}</td>',
        f'<td class="muted">{html.escape(run["note"])}</td>',
    ]
    return f'<tr class="{"latest" if latest else "older"}">{"".join(cells)}</tr>'


def write(runs_dir: Path = RUNS) -> Path:
    """Write runs_dir/index.html from the run.json files in it and return its path."""
    by_bfe: dict[str, list[dict]] = {}
    for run in _runs(runs_dir):
        by_bfe.setdefault(run["bfe"], []).append(run)
    sections = []
    for bfe, runs in sorted(by_bfe.items(), key=lambda kv: max(r["tid"] for r in kv[1]), reverse=True):
        runs.sort(key=lambda r: r["tid"], reverse=True)
        # The latest usable run is the one to open; a fallback run is only on top when nothing better exists
        best = next((r for r in runs if not _warning(r)), runs[0])
        older = [r for r in runs if r is not best]
        body = _row(best, True)
        if older:
            rows = "".join(_row(r, False) for r in older)
            body += (f'<tr class="fold"><td></td><td colspan="5"><details><summary>{len(older)} andre runs</summary>'
                     f'<table>{rows}</table></details></td></tr>')
        sections.append(body)
    table = "".join(sections) or '<tr><td colspan="6" class="muted">Ingen runs endnu.</td></tr>'
    page = PAGE.replace("__ROWS__", table)
    path = runs_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


PAGE = """<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analyzer runs</title>
<style>
  :root { --bg:#f5f5f3; --panel:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#e2e2dd; --warn:#a4501c; --link:#1f5f9e; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1b1b1a; --panel:#242423; --text:#ecece8; --muted:#a3a39c; --line:#3a3a37; --warn:#e39a66; --link:#7fb2e6; }
  }
  body { margin:0; padding:24px 16px; font:14px/1.45 system-ui, sans-serif; background:var(--bg); color:var(--text); }
  main { max-width:1200px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 4px; }
  .muted { color:var(--muted); }
  .warn { color:var(--warn); font-size:12px; }
  a { color:var(--link); }
  code { background:var(--panel); border:1px solid var(--line); border-radius:4px; padding:1px 5px; }
  .wrap { overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:8px; margin-top:16px; }
  table { border-collapse:collapse; width:100%; }
  td, th { text-align:left; padding:8px 10px; vertical-align:top; }
  th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); border-bottom:1px solid var(--line); }
  tr.latest td { border-top:1px solid var(--line); }
  td.bfe { font-weight:600; white-space:nowrap; }
  details table td { padding:4px 10px; }
  summary { cursor:pointer; color:var(--muted); }
</style>
</head>
<body>
<main>
  <h1>Analyzer – ejendomme</h1>
  <div class="muted">Seneste brugbare run pr. ejendom øverst. Ny ejendom: <code>./run.sh --bfe &lt;nr&gt;</code>.
    Siden skrives efter hver run.</div>
  <div class="wrap"><table>
    <tr><th>BFE</th><th>Run</th><th>Åbn</th><th>Metode</th><th>Udearealer</th><th>Note</th></tr>
    __ROWS__
  </table></div>
</main>
</body>
</html>
"""


if __name__ == "__main__":
    print(write())
