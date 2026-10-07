---
projekt: Analyzier
emne: datakilder
opdateret: 2026-10-07
---

# Analyzier: datakilder

Alle kilder vi bruger, overvejer eller har fravalgt. Hver run skriver i `run.json`, hvilke kilder og hvilke datoer/versioner den brugte (se `PROJEKT.md`, afsnit "Runs og kvalitetsevaluering").

Overblik:

| Kilde | Rolle | Status |
|---|---|---|
| Datafordeler MAT | Analyseområde (jordstykker under BFE) | Primær |
| Datafordeler DAR | Adresse → BFE | Hjælp |
| Datafordeler BBR | Bygningsmaske | Valgfri |
| Ortofoto (Dataforsyningen, flere år) | Hovedkilde til klassificering | Primær |
| Danmarks Højdemodel | Højde: græs / buske / træer | Primær |
| Skråfotos | Visuel kontrol | Kontrol |
| Copernicus (alle lag) | Støtte: vegetation, sæson, fugt | Hentes, effekt måles |
| Kommerciel SAR (Umbra m.fl.) | Mulig forbedring af belægning | Kun hvis 85 % ikke nås |
| OpenStreetMap | Kontrol- og træningsdata | Valgfri |
| ESA WorldCover | Baseline at sammenligne med | Valgfri |
| GeoDanmark vektor | Maske og kontrol | Valgfri |
| Google Maps, Esri, Google 3D | Kun visning | Fravalgt til analyse (licens) |

## Matriklen: afgrænsning af ejendommen

- Kort: https://www.matriklen.dk/kort/sfe/5522814
- **BFE-nummeret er projektets primære nøgle.** Det identificerer ejendommen (SFE), og alle jordstykker under BFE-nummeret udgør tilsammen det areal, vi analyserer.
- Adressen bruges kun til at finde BFE-nummeret. En adresse som "Sct Hubertus Vej 6–72" dækker mange husnumre, men de hører under samme BFE.
- Hentes via Datafordeler (MAT), så vi ikke skal klikke i UI.

## Datafordeler: adresser (DAR) og matrikel (MAT)

GraphQL-endpoint fra vores Postman:

```
https://graphql.datafordeler.dk/DAR/{{darVersion}}?apiKey={{apiKey}}
https://graphql.datafordeler.dk/BBR/{{bbrVersion}}?apiKey={{apiKey}}
https://graphql.datafordeler.dk/MAT/{{matVersion}}?apiKey={{apiKey}}
```

- API-nøgle og versioner (DAR, BBR, MAT) ligger i `.env` (se `PROJEKT.md`). Nøglen må aldrig committes.
- MAT (primær): BFE-nummer → alle jordstykker og deres polygoner. Det er analyseområdet.
- DAR (hjælp): adresse → husnummer → jordstykke → BFE-nummer, når vi kun kender adressen.
- BBR (valgfrit): bygninger og deres footprints, som maske.

## Baggrundskort

- **Forvaltningskort** (målet er at kunne levere et forvaltningskort til kunden)
- **Ortofoto, flere år** (Dataforsyningen, ca. 12,5 cm opløsning). Flere årgange gør klassificeringen mere robust og viser ændringer over tid.

## Skråfotos

- https://skraafoto.dataforsyningen.dk/?center=558376.1593372853%2C6369070.902328094&orientation=north&item=2025_81_08_2_0037_00007049_100mm&year=2025
- Bruges til visuel kontrol og til at se under trækroner, hvor ortofotoet kun viser kronen.

## Google Maps (kun reference)

- https://www.google.com/maps/@57.4603815,9.973449,18.25z
- Kun til manuel sammenligning. Google-billeder må ikke indgå i analysen pga. licens.

## Satellit: Copernicus Data Space (hent alle lag)

- Browser: https://browser.dataspace.copernicus.eu/?zoom=18&lat=57.46045&lng=9.97442&themeId=DEFAULT-THEME&datasetId=S2_L2A_CDAS&cloudCoverage=30&dateMode=SINGLE&panel=layers
- Beslutning: vi henter **alle** lag, som Copernicus kan levere for analyseområdet. Så kan vi bagefter måle, hvilke lag der faktisk hjælper klassificeringen.
- Adgang: gratis konto på Copernicus Data Space Ecosystem (CDSE) med OAuth-klient. Hentes via Sentinel Hub Process API (eller openEO/STAC). Der er en månedlig kvote.
- Opløsningen er 10 m eller grovere. Én pixel er 100 m² eller mere. Satellitlagene kan derfor ikke tegne grænsen mellem græs og belægning på en boligafdeling. De bruges som støtte: sæson, vegetationssundhed, fugt og ændringer over tid. Ortofoto og højdemodel er hovedkilderne.

**Sentinel-satellitter**

| Lag (Sentinel Hub) | Hvad | Opløsning | Relevans for os |
|---|---|---|---|
| Sentinel-2 L2A (`S2L2A`) | Optisk, 13 bånd inkl. nær-infrarød, atmosfærekorrigeret | 10–60 m | Høj: NDVI og andre vegetationsindeks, tidsserier over sæsonen |
| Sentinel-2 L1C (`S2L1C`) | Samme, uden atmosfærekorrektion | 10–60 m | Lav: kun hvis L2A mangler |
| Sentinel-1 GRD (`S1GRD`) | Radar (VV/VH), virker gennem skyer og om natten | ca. 10 m | Middel: fugt og struktur, supplerer S2 i overskyede perioder |
| Sentinel-3 OLCI / SLSTR / SYN | Farve og overfladetemperatur | 300 m–1 km | Lav: for groft, men overfladetemperatur kan vise befæstede flader på større skala |
| Sentinel-5P L2 | Atmosfære (NO₂, ozon m.m.) | ca. 5 km | Ingen: hentes kun for fuldstændighedens skyld |

**Andre lag i CDSE**

| Lag | Hvad | Opløsning | Relevans |
|---|---|---|---|
| Landsat 8–9 | Optisk + termisk | 30 m (termisk 100 m) | Lav–middel: længere historik end Sentinel-2 |
| Copernicus DEM (GLO-30/90) | Højdemodel | 30/90 m | Lav: Danmarks Højdemodel er meget bedre |
| CLMS Tree Cover Density | Andel trækroner pr. pixel | 10–100 m afhængigt af version | Middel: kontrol af træ-klassen |
| CLMS Imperviousness | Andel befæstet areal pr. pixel | 10–20 m | Middel: kontrol af belægnings-klassen |
| CLMS Small Woody Features | Hække, trærækker, småbeplantning | 5 m | Middel: buske og hække |
| CLMS Urban Atlas / CORINE / CLC+ | Arealanvendelse | 10 m–100 m | Lav: grov baggrund |
| CLMS HR-VPP | Vegetationsfænologi (vækstsæson) | 10 m | Middel: skelner græs fra træer via sæsonmønster |

Hvilke CLMS-produkter der findes for Danmark og i hvilken version, skal tjekkes i CDSE-kataloget, før vi koder mod dem.

## Kommerciel SAR med højere opløsning end Sentinel-1

Mål: et skøn på belægning med ca. 85 % nøjagtighed. Sentinel-1 har ca. 10 m opløsning. Kommercielle SAR-satellitter ned til 0,25 m findes:

| Udbyder | Opløsning | Pris (offentligt kendt, okt. 2026) |
|---|---|---|
| Umbra (Spotlight) | 1 m / 0,5 m / 0,35 m / 0,25 m | Arkiv: $675 / $950 / $1.750 / $3.250 pr. scene på 5×5 km. Fast prisliste |
| Capella Space | ned til ca. 0,5 m | Pris kun på tilbud. På UP42 fra 29.000 credits pr. arkivscene og 86.300 credits for bestilt optagelse |
| ICEYE | ned til ca. 0,5 m | Pris kun på tilbud. På UP42 fra 28.571 credits (arkiv) og 87.500 credits (bestilt) |
| Airbus TerraSAR-X / PAZ, COSMO-SkyMed | 0,25–1 m | Kun på tilbud |

Prisen for UP42-credits i euro skal tjekkes. Hvis 100 credits = 1 €, koster en arkivscene ca. 290 €.

Én scene på 5×5 km dækker hele testcasen og mange naboafdelinger. Prisen pr. ejendom bliver lav, hvis flere kunder ligger i samme område.

**Vurdering:**

- SAR skelner glat belægning (lavt signal) fra græs og beplantning (højere signal) ud fra overfladens ruhed. Det virker på åbne flader.
- I en boligafdeling giver bygningerne radarskygge og "layover" (bygninger vælter ind over nabofladerne i billedet). Det rammer netop de smalle flader mellem husene.
- Våd eller kortklippet græsplæne og fliser kan ligne hinanden.
- Det er vores vurdering, at SAR alene næppe når 85 % på en boligafdeling. SAR sammen med ortofoto kan godt.
- Ortofoto (12,5 cm, gratis) plus Danmarks Højdemodel er sandsynligvis allerede nok til 85 %. Det skal testes først.

**Anbefaling:** byg og mål først på gratis data (ortofoto + DHM + Sentinel). Køb kun én Umbra-scene ($675–950), hvis vi ikke når 85 %, og mål så forbedringen på testcasen. Tjek også Umbras gratis Open Data-program for eksempelscener, så vi kan teste metoden uden at betale.

## Øvrige åbne datakilder

Vi har gennemgået [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view) (MIT, 19 lag). Næsten alle lagene er realtidsdata (fly, skibe, satellitbaner, jordskælv, kameraer, vejr) og er ikke relevante for udearealer. Følgende kan bruges:

| Kilde | Hvad | Licens | Relevans |
|---|---|---|---|
| OpenStreetMap (Overpass API) | Tags som `landuse=grass`, `natural=wood`, `natural=scrub`, `leisure=playground`, `surface=paving_stones/asphalt`, `amenity=parking` | ODbL (deling af afledte data på samme vilkår) | Middel: gratis træningsdata og kontrol, men dækningen varierer |
| NASA GIBS / HLS | Harmoniserede Sentinel-2 + Landsat-tidsserier | Public domain | Lav: samme data som CDSE |
| Esri World Imagery | Luftfoto som baggrund | Esri-vilkår, kun visning | Ingen til analyse |
| Google Photorealistic 3D | 3D-byer | Google-vilkår | Ingen: licensen tillader ikke analyse |

Forken [Gaia Earth View](https://wiki.p2pfoundation.net/Gaia_Earth_View) tilføjer MODIS-vegetation og SMAP-jordfugt. De har 250 m–9 km opløsning og er for grove til os.

Andre åbne kilder, der er værd at tilføje:

- **ESA WorldCover** (10 m, CC BY 4.0): global arealdække med klasserne træ, busk, græs og befæstet. God som baseline at sammenligne vores resultat med.
- **Danmarks Højdemodel** (se næste afsnit): den vigtigste ekstra kilde.
- **GeoDanmark vektordata** (Dataforsyningen): bygninger, vejkanter og andre tekniske flader som maske og kontrol.

## Forslag: højdemodel (DHM)

- Danmarks Højdemodel (overflademodel minus terrænmodel) giver højden på det, der står på jorden.
- Det er den nemmeste måde at skelne **græs** (lav) fra **buske** (0,5–3 m) og **træer** (> 3 m).

## Research-links

Brugt til gennemgangen 2026-10-07:

- God's Eye View: https://github.com/bilawalsidhu/gods-eye-view (README og DATA_SOURCES.md)
- Gaia Earth View: https://wiki.p2pfoundation.net/Gaia_Earth_View
- CDSE, datasæt: https://documentation.dataspace.copernicus.eu/Data.html
- CDSE, Sentinel Hub-datasæt: https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data.html
- CDSE, CLMS: https://documentation.dataspace.copernicus.eu/Data/CopernicusServices/CLMS.html
- CLMS Tree Cover Density 10 m: https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/clms/land-cover-and-land-use-mapping/tree-cover-and-forests/tree-cover-density/tree-cover-density/clms_vlcc_tree-cover-density_europe_10m_yearly_v1.html
- Umbra-priser (SkyWatch): https://skywatch.com/geospatial-data-providers/umbra/
- Umbra Spotlight: https://umbra.space/remote-sensing/spotlight/
- SAR-udbydere sammenlignet: https://www.observationdata.com/providers/sar-data/
- Capella Space: https://www.observationdata.com/reviews/capella-space/
