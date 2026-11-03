---
projekt: Analyzer
emne: sideprojekt, 3D-model fra skråfotos
status: idé, ikke startet
oprettet: 2026-11-03
ejer: Rico Sørensen
---

# Sideprojekt: 3D-model af ejendommen fra skråfotos

Idé: lav en 3D-model (Gaussian splatting eller farvelagt punktsky) af ejendommen ud fra skråfotos og vis den i browseren. Ikke en del af klassificeringen. Tages op senere.

## Hvad vi allerede har

- **Kameraerne er kendte.** Hvert skråfoto i STAC-API'et har fuld orientering fra flyets GPS/IMU, efterbehandlet af Klimadatastyrelsen: `pers:perspective_center` (EPSG:25832), `pers:rotation_matrix` og `pers:interior_orientation` (brændvidde, hovedpunkt, pixelstørrelse, sensorstørrelse). Vi bruger dem allerede i `analyzer/sources/skraafoto.py` (`project()`) til at tegne ejendomsgrænsen ind. Det sværeste trin i splatting, at regne kamerapositionerne ud (COLMAP), kan derfor springes over.
- **Geometri at starte fra.** DHM-punktskyen (ca. 24 punkter pr. m² på testcasen) kan bruges som startpunkter for splats.
- **Kontrolpunkter.** Bygningsomrids fra GeoDanmark med højde fra DHM giver kendte 3D-punkter (tag- og bygningshjørner). De kan tegnes ind i hvert billede for at måle, om kameraerne rammer, og finjustere dem pr. billede eller pr. årgang.

## Udfordringer

- **For få billeder pr. sted.** I dag henter vi fem (ét lodret og fire skrå). God splatting kræver typisk mindst 30–50 overlappende billeder. Nabobilleder fra samme flyvning kan give ca. 20–40 over ejendommen. Gårde og flader under træer bliver stadig grumsede.
- **Flere årgange (2017–2025, ulige år).** De giver flere vinkler, så bygninger, tage og belægning bliver bedre. Men splatting forudsætter en scene, der står stille: træer vokser, buske klippes, biler flytter sig. Det giver dobbeltkonturer og løse klatter. "In the wild"-varianter (fx Splatfacto-W) kan maskere noget af det og udligne lys. Alle årgange er forårsfotos uden løv, så årstiden er ens.
- **Forskydning mellem årgange.** Hver årgang er geo-refereret for sig og kan ligge cm–dm forskudt. Kameraerne skal finjusteres samlet (fx COLMAP med kendte positioner som start, eller mod bygningshjørnerne), før årgangene blandes.
- **GPU.** Træning kræver GPU (OpenSplat på Mac med M-chip eller en lejet GPU i skyen). Passer ikke ind i den nuværende Docker-pipeline.

## Forslag til rækkefølge

1. **Kontrol af kameraer:** tegn bygningshjørner ind i 2025-billederne. Ligger de pænt, er kameradataene gode nok direkte.
2. **Farvelagt punktsky (ca. en dag):** projicér punktskyen ind i skråfotos og ortofoto, giv hvert punkt en farve, og vis den i Potree eller three.js. Ingen træning.
3. **Splatting på én årgang:** 2025 med nabobilleder fra samme flyvning, punktskyen som start.
4. **Flere årgange:** tilføj 2023 (tættest på 2025, mindst ændring), med samlet finjustering af kameraer.
5. **Visning:** GaussianSplats3D eller Spark (three.js) i `viewer.html` eller en separat side.

## Klimadatastyrelsens 3D Ortofoto (3DO)

- Labs-prototype på Dataforsyningen: https://dataforsyningen.dk/labs/4963. Tredimensionelt luftfoto lavet ud fra skråfotos, hvor facader kan ses.
- Det kan være den færdige udgave af det, vi selv ville lave med splatting. Demoen viser dog Aarhus, ikke testcasen i Hjørring, og format, dækning, licens og adgang er ukendt.
- Beslutning 2026-11-03: vi bygger selv en model først. 3DO tages op igen, hvis KDS svarer, at det dækker landet og må bruges.

## Første skridt: bygningsmodel som baseline

Før billederne laves en 3D-model af bygningerne på matriklen ud fra data, vi allerede har i v0.4:

- Grundplan fra GeoDanmark, højder fra DHM overflade minus terræn, tagform, hældning og retning fra bygningsanalysen, etager, tagmateriale og ydervæg fra BBR. Terræn fra DHM, skåret til matriklen.
- To varianter: en **simpel model** (tagform med målte højder, LOD2) som baseline, og en **målt model** (tagflade direkte fra DHM overflade, kun celler med bygningspunkter i punktskyen). Forskellen mellem dem gemmes pr. bygning som kontrol.
- Bygges i Python i pipelinen og eksporteres som `.glb` (meter, nulpunkt midt på ejendommen). Vises i Unity (glTFast, MCP for Unity fra Coplay) og i `viewer.html` med three.js.

## Sidegevinst: ændringer over tid

Årgangene er nok mere værd til at se ændringer over tid end til 3D: ny belægning, fjernet beplantning, nye bygninger. Det kan bruges direkte i drift og som kontrol af klassificeringen.
