---
projekt: Analyzer
status: idé / oplæg
oprettet: 2026-10-07
ejer: Rico Sørensen
bestiller: Mikael Kirkegaard
---

# Analyzer: AI-beregnede udearealer

## Formål

Vi vil analysere udearealer for vores kunder og automatisk opdele dem i arealtyper:

- **Belægning** (fliser, asfalt, grus, p-pladser, stier)
- **Græs** (plæner)
- **Træer og buske** (beplantning, hække, krat)
- (Bygninger skæres fra, så de ikke tæller med som udeareal)

Resultatet er m² pr. arealtype pr. ejendom, plus et kort med polygoner. Det kan bruges til drift, udbud af grøn pleje og forvaltningskort.

## Testcase

| Felt | Værdi |
|---|---|
| Kunde | Bovendia, Afd. 7 |
| Adresse | Sct Hubertus Vej 6–72, 9800 Hjørring |
| SFE / BFE-nummer | 5522814 |
| Koordinat (WGS84) | ca. 57.46038, 9.97345 |
| Koordinat (EPSG:25832) | ca. 558376, 6369071 |

Kilde: mail fra Mikael Kirkegaard, 2026-10-07, "Test af AI beregnede udearealer".

## Datakilder

Alle kilder med links, licenser, opløsning og vurdering står i [KILDER.md](KILDER.md).

Kort fortalt:

- **Analyseområde:** alle jordstykker under BFE-nummeret, hentet fra Datafordeler MAT. BFE-nummeret er projektets primære nøgle.
- **Hovedkilder:** ortofoto (flere år, 12,5 cm) og Danmarks Højdemodel.
- **Støtte:** alle Copernicus-lag. Vi måler, hvilke der hjælper.
- **Kommerciel SAR:** kun hvis vi ikke når 85 % med gratis data.

## Foreslået pipeline

1. **Input:** BFE-nummer. Kender vi kun adressen, slår vi BFE-nummeret op via DAR.
2. **Opslag:** MAT via Datafordeler (`MAT_SamletFastEjendom`). Ejendommens polygon er analyseområdet. Uden polygon stopper run'en.
3. **Maske:** bygningsomrids fra GeoDanmark (Datafordeler WFS).
4. **Hent billeder:** ortofoto (flere år) for polygonens bounding box. Hent højdemodel og alle Copernicus-lag (se [KILDER.md](KILDER.md)) for samme område. Hent også OSM-tags som kontroldata.
5. **Klassificering:**
   - Første version: regelbaseret, med farve/vegetationsindeks fra ortofoto og højde fra DHM.
   - Næste version: AI-segmentering (fx en fortrænet model til luftfoto, finjusteret på vores egne eksempler).
6. **Output:** polygoner pr. arealtype (GeoJSON), m² pr. type og et kortbillede/forvaltningskort.
7. **Kontrol:** sammenlign med skråfoto og manuel opmåling på testcasen.
8. **Gem:** alt fra kørslen gemmes som en run (se næste afsnit).

## Runs og kvalitetsevaluering

Hver kørsel af pipelinen er en **run**. En run gemmes, så vi kan sammenligne runs og se, om vi nærmer os kvalitetsmålet.

**Kvalitetsmål:** mindst 85 % korrekt klassificeret belægning (krav til græs og træer/buske er endnu ikke aftalt).

### Mappe pr. run

```
runs/
  OVERSIGT.md                      # én linje pr. run: scorer og status mod målet
  _skabelon/evaluering.md          # skabelon til Ricos evaluering
  reference/bfe-5522814.geojson    # facit (kommer senere, se nedenfor)
  2026-10-08_1430_bfe-5522814/
    run.json                       # input, metode/version, parametre, kilder med datoer
    output/arealer.geojson         # polygoner pr. arealtype
    output/arealer.csv             # m² pr. arealtype
    output/kort.png                # resultat oven på ortofoto
    metrics.json                   # automatiske tal (og score mod facit, når det findes)
    evaluering.md                  # Ricos evaluering
    data/                          # hentede rådata (ikke i git, kan være store)
```

- Navngivning: `<dato>_<tid>_bfe-<BFE-nummer>`.
- `run.json` skal gøre en run reproducerbar: git-commit, metode og version, alle parametre, og for hver kilde: hvilken årgang, optagelsesdato og version der blev brugt.
- `data/` står i `.gitignore`. Resten af run-mappen committes, så historikken bevares.

### Evaluering, trin 1: Ricos vurdering

Efter hver run udfylder Rico `evaluering.md` (kopi af `runs/_skabelon/evaluering.md`):

- Score pr. arealtype: hvor stor en andel af arealet er korrekt (0–100 %)?
- Fejl med placering og type, fx "fliser ved nr. 24 er klassificeret som græs".
- Samlet dom: **godkendt** / **tæt på** / **afvist**.

Scorerne skrives ind i `runs/OVERSIGT.md`, så udviklingen kan ses run for run.

### Evaluering, trin 2: facit og automatisk score

Ricos rettelser bliver til et facit:

1. Tag den bedste run og ret polygonerne i fx QGIS, til de er korrekte.
2. Gem den som `runs/reference/bfe-<nr>.geojson`.
3. Herefter beregner hver run selv nøjagtighed pr. arealtype og en fejlmatrix (hvad blev forvekslet med hvad) mod facit. Resultatet lander i `metrics.json` og `OVERSIGT.md`.

Så kan vi afprøve nye kilder og metoder hurtigt og se med det samme, om de flytter os mod 85 %. Ricos vurdering bruges derefter til nye testejendomme uden facit.

## Sådan kører du en run

Kræver Docker og en udfyldt `.env`.

```bash
./run.sh --bfe 5522814 --note "hvad denne run afprøver"
```

- Resultatet lander i `runs/<dato>_<tid>_bfe-<nr>/`. Åbn `viewer.html` i browseren.
- `--omraade fil.geojson` bruger en polygon fra fil i stedet for matriklen.
- `--uden-matrikel --center x,y` giver kun en forhåndsvisning. Arealerne gælder ikke ejendommen.
- `--uden-punktsky` springer punktskyen over. Første gang en ejendom køres, hentes en fil på ca. 350 MB pr. km².
- Koden ligger i `analyzer/`: `sources/` (datakilder), `classify.py` (regler), `outputs.py` (filer og viewer), `run.py` (orkestrering).

## Konfiguration (`.env`)

```dotenv
DATAFORDELER_API_KEY=
DATAFORDELER_DAR_VERSION=
DATAFORDELER_BBR_VERSION=
DATAFORDELER_MAT_VERSION=
DATAFORDELER_GRAPHQL_BASE_URL=https://graphql.datafordeler.dk
CDSE_CLIENT_ID=
CDSE_CLIENT_SECRET=
DATAFORSYNINGEN_TOKEN=
```

- `.env` står i `.gitignore`. `.env.example` committes uden værdier.

## Åbne spørgsmål

1. Hvilke Copernicus-lag giver faktisk bedre klassificering? Vi henter alle og måler effekten på testcasen.
2. Hvad er et "forvaltningskort" præcis for kunden: format, signaturer, krav til detaljer?
3. Hvilke arealtyper skal skelnes? Skal belægning deles op (asfalt, fliser, grus)?
4. Nøjagtighed: ca. 85 % på belægning er nok (Rico, 2026-10-07). Gælder samme krav for græs og træer/buske?
5. Teknologivalg: prototypen er Python i Docker (rasterio, GDAL). Skal den endelige løsning være .NET som resten af stacken?
6. Hvilken produktlinje hører projektet til? Skal det kobles på DKV (lokationer/lejemål) eller være selvstændigt?
7. Skal vi købe kommerciel SAR? Først når gratis data er målt mod 85 %-målet.
8. Har vi adgang til ortofoto og DHM via Dataforsyningen (token), eller skal der oprettes en bruger?

## Første milepæl

Kør pipelinen på Sct Hubertus Vej 6–72 og få m² for belægning, græs og træer/buske. Gem kørslen som en run, vis resultatet som et lag oven på ortofotoet, og lad Rico (og Mikael) evaluere det i `evaluering.md`.
