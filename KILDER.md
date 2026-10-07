---
projekt: Analyzer
emne: datakilder
opdateret: 2026-10-07
---

# Analyzer: datakilder

Alle kilder vi bruger, overvejer eller har fravalgt. Hver run skriver i `run.json`, hvilke kilder og hvilke datoer/versioner den brugte (se `PROJEKT.md`, afsnit "Runs og kvalitetsevaluering").

Overblik (status pr. 2026-10-07, efter første run med rigtige data):

| Kilde | Rolle | Status i pipelinen |
|---|---|---|
| Datafordeler MAT (`MAT_SamletFastEjendom`) | Analyseområde: ejendommens polygon | I brug, virker |
| GeoDanmark vektor (Datafordeler WFS) | Bygningsomrids (maske), træer, p-pladser, hegn | I brug, virker |
| Ortofoto RGB + CIR (Dataforsyningen) | Hovedkilde: farve og vegetationsindeks | I brug, virker |
| Danmarks Højdemodel, terræn + overflade (Dataforsyningen) | Højde: græs / buske / træer | I brug, virker |
| Sentinel-2 L2A (Earth Search, AWS) | Støtte: sommer-NDVI | I brug, virker |
| ESA WorldCover | Baseline | I brug, virker |
| OpenStreetMap (Overpass) | Kontrol | I brug. Overpass er ustabil: retry over 3 servere og cache i 30 dage. Problemer logges i `runs/DRIFTSLOG.md` |
| Copernicus Data Space (øvrige lag) | Støtte: radar, CLMS m.m. | Nøgler sat, ikke bygget endnu |
| Danmarks Højdemodel, punktsky 2023 (Datafordeler) | Afgør træer og buske (laserpunkternes klasser) | I brug fra v0.3, virker |
| Skråfoto 2025 (Dataforsyningen) | Visuel kontrol i viewer, fem retninger | I brug fra v0.3, virker |
| Fotoindex | Optagedato for ortofoto | Kandidat |
| Overture Maps, Meta/WRI Canopy Height, Mapillary | Kontrol og belægningstype | Kandidat (se nederst) |
| Danmarks Arealinformation, Basemap04, Plandata | Baggrund | Kandidat, lav prioritet |
| Datafordeler DAR | Adresse → BFE | Ikke nødvendig når BFE kendes |
| Datafordeler BBR (`BBR_Bygning`) | Tagmateriale, ydervæg, anvendelse, opførelsesår pr. bygning | I brug fra v0.4, virker. Kobles via `BBRUUID` på GeoDanmark-bygningen |
| Kommerciel SAR (Umbra m.fl.) | Mulig forbedring af belægning | Kun hvis 85 % ikke nås |
| Google Maps, Esri, Google 3D | Kun visning | Fravalgt til analyse (licens) |

## Matriklen: afgrænsning af ejendommen

- Kort: https://www.matriklen.dk/kort/sfe/5522814
- **BFE-nummeret er projektets primære nøgle.** Arealer beregnes kun inden for ejendommens polygon. Pipelinen stopper, hvis polygonen ikke kan hentes (eller gives med `--omraade`).
- `MAT_SamletFastEjendom` har ejendommens samlede polygon (`geometri { wkt }`, EPSG:25832). For en samlet fast ejendom er `id_lokalId` lig med BFE-nummeret.
- Kontrol for testcasen: polygonen giver 6.234 m², matriklen oplyser 6.232 m² (ét jordstykke).
- Andre MAT-entiteter: `MAT_Jordstykke` (matrikelnummer, registreret areal, vejareal), `MAT_JordstykkeTemaflade` (fredskov, strandbeskyttelse, klitfredning m.m. som polygoner), `MAT_OptagetVej`.

## Datafordeler: GraphQL og WFS

GraphQL (fra vores Postman):

```
https://graphql.datafordeler.dk/<REGISTER>/<version>?apiKey=<nøgle>
```

- Virkende versioner (testet 2026-10-07): MAT v2 og v3, BBR v2 og v3, GEODKV v2, DAR v3 (fra Postman).
- Skemaer kan hentes uden nøgle: `https://datafordeler.dk/GraphQLSchema/<REGISTER>.graphql` (MAT, BBR, DAR, GEODKV).
- **Begrænsning:** alle GraphQL-forespørgsler skal filtrere på `id_lokalId` (eller `datafordelerRowId`) og angive `virkningstid` eller `registreringstid`. Man kan altså ikke søge "alle bygninger i dette område" via GraphQL.
- Rumlige opslag går derfor via WFS: `https://wfs.datafordeler.dk/GEODKV/GEODKV_WFS/1.0.0/WFS?apikey=<nøgle>` (WFS 2.0, GeoJSON-output, bbox i EPSG:25832). Pipelinen slår typenavnene op i GetCapabilities.
- De gamle WFS-tjenester (`GeoDanmark60_NOHIST_GML3` m.fl. og Dataforsyningens `*_DAF`-WFS) lukkede 1. juli 2026.
- DAWA (`api.dataforsyningen.dk/adresser`, `/jordstykker`) er lukket og svarer 410 Gone.
- API-nøgle og versioner ligger i `.env`. Nøglen må aldrig committes.

## GeoDanmark vektor

Landsdækkende kortdata (GeoDanmark 6.0, 63 objekttyper), opdateret af kommunerne og Klimadatastyrelsen. Det er sikkert de samme bygningsomrids, som kort.bbr.dk viser.

Relevante objekttyper og hvordan pipelinen bruger dem:

| Objekttype | Geometri | Brug | Fundet på testcasen |
|---|---|---|---|
| `Bygning` | Flade | Bygningsmaske (autoritativ) | 26 |
| `Trae` | Punkt | Træ (3 m radius) | 39 |
| `Traegruppe` | Flade | Træer | 1 |
| `Skov` | Flade | Træer | 1 |
| `KratBevoksning` | Flade | Buske | 0 |
| `Parkering`, `Plads` | Flade | Belægning | 1 / 0 |
| `Vejkant` | Linje | Kun visning (kontrol af belægning) | 23 |
| `Hegn` | Linje | Kun visning (kan være hæk eller fast hegn) | 74 |
| `Sportsbane`, `RekreativtOmraade`, `Bassin`, `Soe` | Flade | Kun visning | 0 |

Fuld liste: https://grunddatamodel.datafordeler.dk/objekttypekatalog/GeoDanmark/package-summary.html

## Ortofoto (Dataforsyningen)

- WMS: `https://api.dataforsyningen.dk/orto_foraar_DAF?token=<token>`. GetCapabilities virker uden token, billeder kræver token.
- Lag pr. år: `geodanmark_<år>_12_5cm` (RGB) og `geodanmark_<år>_12_5cm_cir` (nær-infrarød, rød, grøn). Årgange 2015–2025.
- CIR-laget giver vegetationsindeks (NDVI) i 12,5 cm. Det er vores vigtigste kilde til græs/belægning.
- **Vigtigt:** det er forårsfotos. Løvtræer er uden blade og har derfor lavt vegetationsindeks. Højdemodellen skal afgøre, om noget er et træ.
- Skygger fra bygninger gør vegetationsindekset usikkert. Flere årgange og sommerdata (Sentinel-2) kan hjælpe.

## Danmarks Højdemodel (Dataforsyningen)

- WCS 1.0.0: `https://api.dataforsyningen.dk/dhm_wcs_DAF?token=<token>`, coverages `dhm_terraen` og `dhm_overflade` (0,4 m).
- Overflade minus terræn = højde over terræn. Græs < 0,5 m, buske 0,5–3 m, træer > 3 m.
## Danmarks Højdemodel, punktsky (Datafordeler)

- REST-API (nøgle: `DATAFORDELER_API_KEY`):
  - Find filer: `https://api.datafordeler.dk/FileDownloads/v1.0/GetAvailablePointCloudFileDownloads?Version=1&MinX=..&MinY=..&MaxX=..&MaxY=..&apiKey=..` (EPSG:25832).
  - Hent fil: `https://api.datafordeler.dk/FileDownloads/v1.0/GetPointCloudFile?Id=<id>&apiKey=..`
- Datasæt: `Punktsky` (gældende), `Punktsky2023`, `Punktsky2015`, `Punktsky2007`. Pipelinen bruger `Punktsky2023`.
- Filer á 1×1 km i LAZ. Testcasens fil `PUNKTSKY_1km_6369_558.laz` er 349 MB. Den gemmes i `cache/dhm/` og hentes kun én gang.
- **Faldgrube:** API'et svarer `application/zip`, men sender selve LAZ-filen. Pipelinen kigger på de første bytes (`LASF`).
- Målt på testcasen: 24 punkter pr. m². Fordeling i området: 36 % terræn, 48 % høj vegetation, 6 % mellem vegetation, 1 % lav vegetation, 7 % bygning.
- Klasser (ASPRS): 2 terræn, 3 lav, 4 mellem og 5 høj vegetation, 6 bygning, 9 vand.
- **Faldgrube:** biler i scanningen fra 2023 er ofte klassificeret som mellem vegetation. Buske fra punktskyen kræver derfor også lidt grønt i ortofotoet (NDVI > 0).

## Andre danske offentlige kilder

- **Skråfoto-API (Dataforsyningen), i brug:** STAC-API `https://api.dataforsyningen.dk/rest/skraafoto_api/v1.0` (token i header `token`). Samlinger `skraafotos2017` til `skraafotos2025`. Billederne er COG-filer på `skraafoto-cdn.dataforsyningen.dk` og kan læses uden token. Hvert billede har kameraparametre (`pers:*`), så ejendomsgrænsen kan tegnes ind. Testcasens 2025-billeder er fra 6. april 2025 (løvfrit).
- **Fotoindex (Dataforsyningen, lag `fotoindex_<år>`):** optagetidspunkt for ortofotoet. Skal med i `run.json`.
- **Danmarks Arealinformation (Miljøportalen):** § 3-beskyttet natur, fredskov m.m. Relevant for drift, ikke for klassificering. https://miljoeportal.dk/en/danmarks-miljoeportal-s-systems/danmarks-arealinformation
- **Basemap04 (DCE, Aarhus Universitet):** arealanvendelse i 10 m-raster. Groft, men kan bruges som baseline ved siden af WorldCover. https://envs.au.dk/en/research-areas/society-environment-and-resources/land-use-and-gis/basemap
- **Plandata.dk:** lokalplaner, fx krav om grønne arealer.
- **BBR:** bygningsdata som attributter (anvendelse, tagmateriale `byg033`, ydervæg `byg032`, opførelsesår, bebygget areal). Omridsene ligger i GeoDanmark, ikke i BBR. GeoDanmark-bygningen har feltet `BBRUUID`, som er BBR-bygningens `id_lokalId`. Pipelinen slår op med `id_lokalId: { in: [...] }` (højst 100 pr. kald) og sender både `virkningstid` og `registreringstid`, så kun den gældende version kommer med. Testcasen: 8 bygninger på ejendommen, alle med BBR-data. Tagform, hældning og højde står ikke i BBR; dem beregner pipelinen ud fra DHM overflade og punktskyen.

## Baggrundskort

- **Forvaltningskort** (målet er at kunne levere et forvaltningskort til kunden).

## Skråfotos

- https://skraafoto.dataforsyningen.dk/?center=558376.1593372853%2C6369070.902328094&orientation=north&item=2025_81_08_2_0037_00007049_100mm&year=2025
- Bruges til visuel kontrol og til at se under trækroner, hvor ortofotoet kun viser kronen.

## Google Maps (kun reference)

- https://www.google.com/maps/@57.4603815,9.973449,18.25z
- Kun til manuel sammenligning. Google-billeder må ikke indgå i analysen pga. licens.

## Satellit: Copernicus Data Space (hent alle lag)

- Browser: https://browser.dataspace.copernicus.eu/?zoom=18&lat=57.46045&lng=9.97442&themeId=DEFAULT-THEME&datasetId=S2_L2A_CDAS&cloudCoverage=30&dateMode=SINGLE&panel=layers
- Beslutning: vi henter **alle** lag, som Copernicus kan levere for analyseområdet. Så kan vi bagefter måle, hvilke lag der faktisk hjælper klassificeringen.
- Adgang: gratis konto på Copernicus Data Space Ecosystem (CDSE) med OAuth-klient. Hentes via Sentinel Hub Process API (eller openEO/STAC).
- Kvote for gratis konto: 10.000 requests og 10.000 processing units pr. måned, 300 af hver pr. minut. Der er ingen betaling: når kvoten er brugt, afvises kald til næste måned. Mere kvote kræver køb af "commercial cloud credits". https://documentation.dataspace.copernicus.eu/Quotas.html
- Pipelinen skal have et loft over kald pr. run og logge forbruget i `run.json`.
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

Earth Search (AWS) leverer Sentinel-2 L2A uden nøgle og bruges allerede. CDSE er derfor kun nødvendig for de øvrige lag.

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
- **Danmarks Højdemodel**: se afsnittet ovenfor. Den vigtigste ekstra kilde.
- **GeoDanmark vektordata** (Dataforsyningen): bygninger, vejkanter og andre tekniske flader som maske og kontrol.

## Kandidater, ikke undersøgt i dybden

- **Overture Maps Foundation:** åbne kortdata (bygninger, arealanvendelse, arealdække), bygget på bl.a. OSM. Kan hentes uden nøgle som GeoParquet. Mest kontrol, ligesom OSM. Licens ODbL/CDLA afhængigt af tema. https://overturemaps.org
- **Meta/WRI Global Canopy Height:** trækronehøjde i ca. 1 m opløsning, beregnet med AI fra satellitbilleder. Danmarks Højdemodel er bedre her, men den kan bruges som sammenligning. Licens CC BY 4.0.
- **Mapillary:** gadebilleder fra brugere, gratis API med nøgle. Kan vise belægningstypen (fliser, asfalt, grus) fra jordhøjde. Dækning skal tjekkes for den enkelte ejendom.

## Research-links

Brugt 2026-10-07:

- Datafordeler GraphQL-skemaer: https://datafordeler.dk/GraphQLSchema/MAT.graphql (og BBR, DAR, GEODKV)
- GeoDanmark Vektor WFS-entiteter: https://datafordeler.dk/dataoversigt/geodanmark-vektor/geodanmark-vektor-wfs-entiteter/
- Lukning af DAF-WFS: https://dataforsyningen.dk/news/5124
- Datafordeler fildownload (punktsky): https://datafordeler.dk/dataoversigt/danmarks-hoejdemodel-dhm/dhm-fildownload-punktsky/
- CDSE-kvoter: https://documentation.dataspace.copernicus.eu/Quotas.html
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
