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
python3 -m analyzer.cleanup                    # oprydning i runs/: viser kun, --udfoer rydder op (se --help)
```

Lokalt uden Docker: opret `.venv/` (git-ignoreret), kør `pip install -r requirements.txt` og derefter `python -m analyzer.run ...` med `.env` indlæst. Der er ingen tests, linter eller build-trin. En ændring verificeres med en run og `viewer.html`.

Kræver `.env` (skabelon i `.env.example`):
- `DATAFORSYNINGEN_TOKEN`: ortofoto, DHM og skråfotos. Uden den falder pipelinen tilbage til Sentinel-2-metoden på 1 m grid i stedet for 0,25 m.
- `DATAFORDELER_API_KEY` + `DATAFORDELER_<DAR|BBR|MAT>_VERSION`: MAT (ejendommens polygon), BBR og punktsky. Uden MAT kræves `--omraade` eller `--uden-matrikel`.
- `CDSE_CLIENT_ID`/`CDSE_CLIENT_SECRET`: Sentinel-2.

Dataforsyningens WMS hentes i ét kald pr. lag med højst 4000 px pr. side (`MAX_PIXELS`), dvs. ca. 1 km ved 0,25 m.

## Arkitektur

- `analyzer/run.py`: orkestrering i én `main()`. Rækkefølge: analyseområde (MAT eller `--omraade`) → `Grid` → kilder → klassificering → maskering til ejendommen → outputs → bygninger og 3D-model → `run.json`, `metrics.json`, `evaluering.md`, en ny linje i `runs/OVERSIGT.md`.
- `analyzer/grid.py`: `Grid` er det fælles analysegrid i EPSG:25832. Alle kilder returnerer rastere på præcis dette grid (`grid.shape`, `grid.transform`). Ny kilde: lever data på `Grid`, ikke i kildens egen projektion.
- `analyzer/sources/`: én fil pr. kilde. En kilde, der ikke kan bruges (manglende nøgle, ingen data), rejser `SourceUnavailable`. `Sources.fetch()` i `run.py` fanger både den og andre fejl, logger status i `run.json` og lader run'en fortsætte. Kun et manglende analyseområde eller ingen billeddata stopper en run.
- `analyzer/classify.py`: regelbaserede metoder. `DEFAULTS` holder alle parametre pr. metode, og de skrives i `run.json`. Metoden vælges ud fra tilgængelige data:
  - `ortho_dhm` (hovedmetode): NDVI fra CIR-ortofoto + nDSM (DHM overflade minus terræn). Punktskyens ASPRS-vegetationsklasser afgør træer/buske, når den findes. Bygningsmaske fra GeoDanmark, OSM som fallback.
  - `sentinel2` (fallback uden token): Sentinel-2 NDVI + ESA WorldCover.
- `analyzer/buildings.py`: bygninger på ejendommen (fra v0.4). GeoDanmark-omrids kobles til BBR via `BBRUUID` (tagmateriale, ydervæg, anvendelse, år). Tagform, hældning, tagareal og højde beregnes fra DHM overflade. Kun celler med bygningspunkter i punktskyen (klasse 6) bruges, så træer over tag springes over. Højde måles mod terræn i en ring uden for omridset, fordi DTM er interpoleret under bygninger. Output: `bygninger.csv`, `bygninger.geojson`, lag `tagmateriale.png` og hover i viewer. Har sin egen `DEFAULTS`, som skrives i `run.json` som `bygning_parametre`.
- `analyzer/paving.py`: belægningstyper inden for klassen belægning (udkast fra v0.6, ikke tjekket mod facit): asfalt, fliser/sten/beton, grus, ukendt. Regelbaseret fra punktskyens intensitet (terrænpunkter, relativt til medianen), ortofotoets lysstyrke og farve (ikke i skygge) og NDVI. Output: `belaegning.tif`, lag `belaegning.png`, hover i viewer, `belaegningstyper_m2` i `metrics.json`. Egen `DEFAULTS`, skrives i `run.json` som `belaegning_parametre`.
- `sideprojekt_3d/model3d.py`: sideprojekt, 3D-model af ejendommen (fra v0.5). Ligger uden for `analyzer/`, men kaldes fra `run.py`, så output havner i run'en. Noter og plan i `sideprojekt_3d/README.md`. `fit_roofs()` vælger en parametrisk tagform pr. bygning og tilpasser tagfod og tagryg til DHM overflade med mindste kvadrater (skorstene og træer sorteres fra). L-, T- og U-formede omrids deles i fløje (`_wings()`, fra v0.7) med ét tag pr. fløj; taget er det højeste af fløjenes tage (`_roof_z()`), og `_composite_regions()` deler det i plane stykker langs skotrenderne. Tagudhæng (fra v0.8): `estimate_overhang()` rykker væggene ind, til omridset har BBR's bebyggede areal (fælles vægge bliver stående), og `_overhang_mesh()` tegner stern, underside og vægge. Felterne `model_*` kommer i `bygninger.csv`. `write()` skriver `output/model/`: `terraen.glb`, `terraen_klasser.glb`, `bygninger.glb` (simpel model, BBR-farver, BBR-data som glTF-extras pr. bygning), `tag_maalt.glb` (tagflade fra DHM, farvet efter afvigelse), `bygninger_skraafoto.glb` (skråfotos som tekstur), `traeer.glb` (stamme og krone pr. træ fra GeoDanmark `Trae` og toppe i kronehøjdemodellen), `traeer_maalt.glb` (punktskyens vegetationspunkter som glTF-punkter), `model.json` (origo i EPSG:25832/DVR90) og en selvstændig `model.html` (three.js). Carport og fritliggende overdækning (BBR 920/950) tegnes åbne: tag på stolper. Overdækkede gennemgange mellem bygninger med fælles væg findes i DHM overflade (`find_passages()`) og skæres ud af bygningerne, kun i 3D. Terrænet får ortofotoet i 10 cm som tekstur, hentet separat i `run.py`. Skråfotos bruges kun på bygninger og ikke på bygninger opført i fotoets år eller senere. glTF er Y-op: x = øst, y = op, z = syd, meter fra origo. GLB skrives uden ekstra afhængigheder; triangulering med `shapely.constrained_delaunay_triangles`.
- `analyzer/sources/skraafoto.py`: henter skråfotos over ejendommen (STAC) og projicerer omridset ind i billederne. Kun til visuel kontrol, indgår ikke i klassificeringen.
- `analyzer/classes.py`: klassekoder i `klasser.tif`. `0 = UDENFOR` (uden for ejendommen). `BYGNING` tæller ikke som udeareal (`OUTDOOR`).
- `analyzer/outputs.py`: GeoTIFF, GeoJSON-polygoner, CSV, PNG-lag og den selvstændige `viewer.html`. `LAYER_INFO` og legender til viewer-lagene ligger her. Funktionerne `agreement()` (mod GeoDanmark/OSM, ikke facit) og `accuracy()` (mod `runs/reference/bfe-<nr>.geojson`) beregner metrics.

## Runs

- `runs/index.html` er forsiden til at skifte mellem ejendomme: én række pr. BFE med seneste brugbare run og links til kort og 3D. `analyzer/index.py` skriver den efter hver run. Byg den alene med `python -m analyzer.index`.
- Hver kørsel skriver `runs/<YYYY-MM-DD_HHMM>_bfe-<nr>/`. `runs/` er git-ignoreret og ligger kun lokalt, undtagen `runs/_skabelon/` (bruges af `run.py`) og `runs/reference/` (facit).
- `run.json` skal gøre en run reproducerbar: version (`analyzer/__init__.py` `VERSION`), git-commit, parametre, kilder med årgang/datoer. Bump `VERSION` ved ændring af metode eller parametre.
- Fejltekster passerer `redact()`, før de skrives til run-filer eller `runs/DRIFTSLOG.md`. Brug den også i nye skrivesteder, da URL'er kan indeholde nøgler.
- `runs/DRIFTSLOG.md` får automatisk en linje ved problemer med datakilder (fx Overpass).
- `evaluering.md` kopieres fra `runs/_skabelon/evaluering.md` og udfyldes af Rico. Pipelinen overskriver den aldrig.
- Kvalitetsmål: mindst 85 % korrekt klassificeret belægning.

## Cache

`cache/` er git-ignoreret. `cache/osm/` gemmer Overpass-svar i 30 dage og bruges også som gammel fallback, når alle servere fejler. `cache/dhm/` gemmer punktsky-tiles (store LAZ-zip-filer). Slet ikke `cache/dhm/` uden grund: genhentning er langsom.
