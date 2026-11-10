"""Clean up runs/: delete chosen run folders, drop overview rows for runs that no longer exist, rebuild the index.

Shows what it would do and changes nothing unless --udfoer is given. Uses only the standard library, so it also
runs outside Docker:

    python3 -m analyzer.cleanup                          # dry run: only rows for missing runs
    python3 -m analyzer.cleanup --note slet --udfoer     # delete runs whose note contains "slet"
    python3 -m analyzer.cleanup --behold-seneste 3       # keep the 3 newest runs per BFE
    python3 -m analyzer.cleanup --mappe 2026-11-10_1305_bfe-4208619 --udfoer

Never touches runs/_skabelon, runs/reference, DRIFTSLOG.md or files that are not run folders.
"""
import argparse
import json
import re
import shutil
from pathlib import Path

from . import index

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
CACHE_DHM = ROOT / "cache" / "dhm"
RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_bfe-[^/\\]+$")
ROW = re.compile(r"^\| \[([^\]]+)\]\(")


def _run_folders(runs_dir: Path) -> list[Path]:
    return sorted(p for p in runs_dir.iterdir() if p.is_dir() and RUN_NAME.match(p.name))


def _info(folder: Path) -> dict:
    try:
        return json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def choose(runs_dir: Path, folders: list[str], note: str | None, keep_latest: int | None) -> list[Path]:
    """Run folders to delete."""
    runs = _run_folders(runs_dir)
    chosen = {runs_dir / name for name in folders if (runs_dir / name) in runs}
    unknown = [name for name in folders if (runs_dir / name) not in runs]
    for name in unknown:
        print(f"  findes ikke som run-mappe: {name}")
    if note:
        chosen |= {p for p in runs if note.lower() in str(_info(p).get("note") or "").lower()}
    if keep_latest is not None:
        by_bfe: dict[str, list[Path]] = {}
        for p in runs:
            by_bfe.setdefault(p.name.split("_bfe-", 1)[1], []).append(p)
        for group in by_bfe.values():
            chosen |= set(sorted(group)[:-keep_latest] if keep_latest > 0 else group)
    return sorted(chosen)


def main() -> None:
    ap = argparse.ArgumentParser(description="Ryd op i runs/. Viser kun, hvad der ville ske, uden --udfoer.")
    ap.add_argument("--mappe", action="append", default=[], help="run-mappe der skal slettes (kan gentages)")
    ap.add_argument("--note", help="slet runs, hvis note indeholder denne tekst (store/små bogstaver er ligegyldige)")
    ap.add_argument("--behold-seneste", type=int, metavar="N", help="behold kun de N nyeste runs pr. BFE")
    ap.add_argument("--part-filer", action="store_true",
                    help="slet også afbrudte punktsky-downloads (cache/dhm/*.part)")
    ap.add_argument("--udfoer", action="store_true", help="udfør oprydningen (ellers kun visning)")
    args = ap.parse_args()

    delete = choose(RUNS, args.mappe, args.note, args.behold_seneste)
    remaining = {p.name for p in _run_folders(RUNS)} - {p.name for p in delete}
    overview = RUNS / "OVERSIGT.md"
    lines = overview.read_text(encoding="utf-8").splitlines(keepends=True) if overview.exists() else []
    drop = {i for i, ln in enumerate(lines) if (m := ROW.match(ln)) and m.group(1) not in remaining}
    dropped = [lines[i] for i in sorted(drop)]
    parts = sorted(CACHE_DHM.glob("*.part")) if args.part_filer and CACHE_DHM.exists() else []

    print(f"Run-mapper der slettes: {len(delete)}")
    for p in delete:
        info = _info(p)
        print(f"  {p.name}  v{info.get('analyzer_version', '?')}  {info.get('note') or ''}")
    print(f"Linjer i OVERSIGT.md der fjernes (run findes ikke længere): {len(dropped)}")
    for ln in dropped:
        print(f"  {ROW.match(ln).group(1)}")
    if args.part_filer:
        print(f"Afbrudte downloads der slettes: {len(parts)}")
        for p in parts:
            print(f"  {p.name}  {p.stat().st_size / 1e6:,.0f} MB")
    if not (delete or dropped or parts):
        print("Intet at rydde op.")
        return
    if not args.udfoer:
        print("Kun visning. Kør igen med --udfoer for at rydde op.")
        return

    for p in delete:
        shutil.rmtree(p)
    if drop:
        overview.write_text("".join(ln for i, ln in enumerate(lines) if i not in drop), encoding="utf-8")
    for p in parts:
        p.unlink()
    print(f"Ryddet op. Forsiden er skrevet igen: {index.write(RUNS).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
