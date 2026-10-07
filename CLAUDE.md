# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projektet

Analyzer klassificerer udearealer på én ejendom (BFE-nummer) i belægning, græs, buske og træer og giver m² pr. arealtype. Det er en Python-prototype. Produktlinjen er ikke afklaret: projektet er ikke koblet til DKV, Backoffice eller HousingPresentation. Formål, testcase og evalueringsproces står i `PROJEKT.md`. Alle datakilder med licens, opløsning og status står i `KILDER.md`. Opdatér `KILDER.md`, når en kilde tilføjes eller skifter status.

Sprog: dokumentation, CLI-flag, run-filer og brugervendt output er på dansk. Kode, docstrings og kommentarer er på engelsk.

## Kommandoer

Pipelinen kører i Docker. `run.sh` bygger imaget og sender argumenterne videre til `python -m analyzer.run`. Repoet mountes som `/work`, så runs lander direkte i `runs/`.

```bash
./run.sh --bfe 5522814 --note "hvad denne run afprøver"   # normal run, ejendom fra MAT
./run.sh --bfe 5522814 --uden-punktsky                     # spring punktskyen over (ca. 350 MB/km² første gang)
./run.sh --bfe 5522814 --omraade fil.geojson               # polygon fra fil (EPSG:4326 eller 25832)
./run.sh --bfe 5522814 --uden-matrikel --center 558376,6369071 --size 400   # kun forhåndsvisning
```

Lokalt uden Docker: `.venv/` findes. Kør `pip install -r requirements.txt` og derefter `python -m analyzer.run ...` med `.env` indlæst. Der er ingen tests, linter eller build-trin. En ændring verificeres med en run og `viewer.html`.

Kræver `.env` (skabelon i `.env.example`). Uden `DATAFORSYNINGEN_TOKEN` falder pipelinen tilbage til Sentinel-2-metoden på 1 m grid i stedet for 0,25 m.

## Arkitektur

- `analyzer/run.py`: orkestrering i én `main()`. Rækkefølge: analyseområde (MAT eller `--omraade`) → `Grid` → kilder → klassificering → maskering til ejendommen → outputs → `run.json`, `metrics.json`, `evaluering.md`, en ny linje i `runs/OVERSIGT.md`.
- `analyzer/grid.py`: `Grid` er det fælles analysegrid i EPSG:25832. Alle kilder returnerer rastere på præcis dette grid (`grid.shape`, `grid.transform`). Ny kilde: lever data på `Grid`, ikke i kildens egen projektion.
- `analyzer/sources/`: én fil pr. kilde. En kilde, der ikke kan bruges (manglende nøgle, ingen data), rejser `SourceUnavailable`. `Sources.fetch()` i `run.py` fanger både den og andre fejl, logger status i `run.json` og lader run'en fortsætte. Kun et manglende analyseområde eller ingen billeddata stopper en run.
- `analyzer/classify.py`: regelbaserede metoder. `DEFAULTS` holder alle parametre pr. metode, og de skrives i `run.json`. Metoden vælges ud fra tilgængelige data:
  - `ortho_dhm` (hovedmetode): NDVI fra CIR-ortofoto + nDSM (DHM overflade minus terræn). Punktskyens ASPRS-vegetationsklasser afgør træer/buske, når den findes. Bygningsmaske fra GeoDanmark, OSM som fallback.
  - `sentinel2` (fallback uden token): Sentinel-2 NDVI + ESA WorldCover.
- `analyzer/classes.py`: klassekoder i `klasser.tif`. `0 = UDENFOR` (uden for ejendommen). `BYGNING` tæller ikke som udeareal (`OUTDOOR`).
- `analyzer/outputs.py`: GeoTIFF, GeoJSON-polygoner, CSV, PNG-lag og den selvstændige `viewer.html`. `LAYER_INFO` og legender til viewer-lagene ligger her. Funktionerne `agreement()` (mod GeoDanmark/OSM, ikke facit) og `accuracy()` (mod `runs/reference/bfe-<nr>.geojson`) beregner metrics.

## Runs

- Hver kørsel skriver `runs/<YYYY-MM-DD_HHMM>_bfe-<nr>/`. Run-mappen committes. `data/` i run-mappen er git-ignoreret.
- `run.json` skal gøre en run reproducerbar: version (`analyzer/__init__.py` `VERSION`), git-commit, parametre, kilder med årgang/datoer. Bump `VERSION` ved ændring af metode eller parametre.
- Fejltekster passerer `redact()`, før de skrives til run-filer eller `runs/DRIFTSLOG.md`. Brug den også i nye skrivesteder, da URL'er kan indeholde nøgler.
- `runs/DRIFTSLOG.md` får automatisk en linje ved problemer med datakilder (fx Overpass).
- `evaluering.md` kopieres fra `runs/_skabelon/evaluering.md` og udfyldes af Rico. Pipelinen overskriver den aldrig.
- Kvalitetsmål: mindst 85 % korrekt klassificeret belægning.

## Cache

`cache/` er git-ignoreret. `cache/osm/` gemmer Overpass-svar i 30 dage og bruges også som gammel fallback, når alle servere fejler. `cache/dhm/` gemmer punktsky-tiles (store LAZ-zip-filer). Slet ikke `cache/dhm/` uden grund: genhentning er langsom.
