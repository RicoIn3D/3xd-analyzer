"""Danmarks Højdemodel point cloud (Datafordeler file download).

Tiles are 1x1 km LAZ files (~350 MB for the 2023 flight), delivered as zip. They are cached in
cache/dhm/ and only the points inside the analysis grid are read. Points carry ASPRS classes:
2 ground, 3 low vegetation, 4 medium vegetation, 5 high vegetation, 6 building, 9 water.
"""

import os
import zipfile
from pathlib import Path

import laspy
import numpy as np
import requests

from ..grid import Grid
from . import SourceUnavailable

API = "https://api.datafordeler.dk/FileDownloads/v1.0"
CACHE_DIR = Path(__file__).resolve().parents[2] / "cache" / "dhm"
CLASSES = {2: "terræn", 3: "lav vegetation", 4: "mellem vegetation", 5: "høj vegetation", 6: "bygning", 9: "vand"}


def _key() -> str:
    key = os.environ.get("DATAFORDELER_API_KEY", "").strip()
    if not key:
        raise SourceUnavailable("DATAFORDELER_API_KEY mangler i .env")
    return key


def _tiles(grid: Grid, dataset: str) -> list[dict]:
    r = requests.get(f"{API}/GetAvailablePointCloudFileDownloads", timeout=60, params={
        "Version": 1, "MinX": grid.xmin, "MinY": grid.ymin, "MaxX": grid.xmax, "MaxY": grid.ymax,
        "apiKey": _key()})
    if r.status_code != 200:
        raise SourceUnavailable(f"Punktsky-katalog: HTTP {r.status_code}")
    tiles = [f for f in r.json().get("availableFileDownloads", []) if f["dataSetName"] == dataset]
    if not tiles:
        raise SourceUnavailable(f"Ingen punktsky-filer i {dataset} for området")
    return tiles


def _download(tile: dict, dataset: str) -> Path:
    """Return the cached LAZ file, downloading and unzipping it the first time."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    laz = CACHE_DIR / f"{dataset}_{tile['fileName']}"
    if laz.exists():
        return laz
    zpath = laz.with_suffix(".zip")
    if not zpath.exists():
        tmp = zpath.with_suffix(".part")
        with requests.get(f"{API}/GetPointCloudFile", params={"Id": tile["id"], "apiKey": _key()},
                          stream=True, timeout=(30, 600)) as r:
            if r.status_code != 200:
                raise SourceUnavailable(f"Punktsky-download {tile['fileName']}: HTTP {r.status_code}")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        tmp.rename(zpath)
    # The API says application/zip, but sends the LAZ file itself (starts with "LASF")
    with zpath.open("rb") as f:
        if f.read(4) == b"LASF":
            zpath.rename(laz)
            return laz
    with zipfile.ZipFile(zpath) as z:
        member = next(n for n in z.namelist() if n.lower().endswith((".laz", ".las")))
        with z.open(member) as src, laz.open("wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    return laz


def punktsky(grid: Grid, dataset: str = "Punktsky2023") -> dict:
    """Count points per class in each grid cell."""
    counts = {c: np.zeros(grid.shape, dtype="uint32") for c in CLASSES}
    total = 0
    files = []
    for tile in _tiles(grid, dataset):
        path = _download(tile, dataset)
        files.append(tile["fileName"])
        with laspy.open(path) as f:
            for pts in f.chunk_iterator(5_000_000):
                x, y = np.asarray(pts.x), np.asarray(pts.y)
                m = (x >= grid.xmin) & (x < grid.xmax) & (y > grid.ymin) & (y <= grid.ymax)
                if not m.any():
                    continue
                col = ((x[m] - grid.xmin) / grid.res).astype(int)
                row = ((grid.ymax - y[m]) / grid.res).astype(int)
                cls = np.asarray(pts.classification)[m]
                idx = row * grid.width + col
                total += int(m.sum())
                for c in CLASSES:
                    sel = cls == c
                    if sel.any():
                        counts[c] += np.bincount(idx[sel], minlength=grid.width * grid.height).reshape(
                            grid.shape).astype("uint32")
    if total == 0:
        raise SourceUnavailable("Punktsky: ingen punkter inden for området")
    area_m2 = (grid.xmax - grid.xmin) * (grid.ymax - grid.ymin)
    return {"antal": counts, "punkter": total, "tæthed_pr_m2": round(total / area_m2, 1), "filer": files,
            "datasæt": dataset,
            "fordeling_pct": {CLASSES[c]: round(100 * float(v.sum()) / total, 1) for c, v in counts.items()}}
