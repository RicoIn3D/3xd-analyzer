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

**Status 2026-11-03: bygget i v0.5.0** (`sideprojekt_3d/model3d.py`, output i run'ens `output/model/`). Testcasen: 8 bygninger. Tøndetagene afviger 0,22–0,39 m i snit fra DHM efter tilpasning med mindste kvadrater. To bygninger er usikre og får fladt tag: én dækket af træer, og en overdækning fra 2025 (bygget efter punktskyen), hvor højden kommer fra GeoDanmarks fotogrammetrisk målte tagkant (±0,15 m). GeoDanmarks tagkant bruges kun, når den er målt (ikke `Manuel`); ellers BBR-etager.

**Dobbelttjek 2026-11-03:** alle 8 GeoDanmark-bygninger ligger helt på ejendommen og har BBR (ingen delvist udeladt). Usikre tage (dækket af træer eller bygget efter skanningen) får ikke højde fra DHM, som ellers måler trækronerne: udhuset (nr. 7) gik fra 6,1 m til 3,0 m (BBR-etager). GeoDanmark-omridset er målt ved tagkanten, så det er 8–25 % større end BBR's bebyggede areal (ydervæg); facaderne i modellen står derfor ved tagkanten. Ikke tjekket: BBR-bygninger på ejendommen uden omrids i GeoDanmark (GraphQL kan kun slå op på id). Næste skridt: åbn `bygninger.glb` i Unity (glTFast) og sammenlign med CityWeft's gratis forhåndsvisning.

Før billederne laves en 3D-model af bygningerne på matriklen ud fra data, vi allerede har i v0.4:

- Grundplan fra GeoDanmark, højder fra DHM overflade minus terræn, tagform, hældning og retning fra bygningsanalysen, etager, tagmateriale og ydervæg fra BBR. Terræn fra DHM, skåret til matriklen.
- To varianter: en **simpel model** (tagform med målte højder, LOD2) som baseline, og en **målt model** (tagflade direkte fra DHM overflade, kun celler med bygningspunkter i punktskyen). Forskellen mellem dem gemmes pr. bygning som kontrol.
- Bygges i Python i pipelinen og eksporteres som `.glb` (meter, nulpunkt midt på ejendommen). Vises i Unity (glTFast, MCP for Unity fra Coplay) og i `viewer.html` med three.js.

## Unity-opsætning

MCP-server valgt 2026-11-03: **MCP for Unity fra Coplay**, https://github.com/CoplayDev/unity-mcp, version `v10.3.0` (MIT). Giver Claude Code adgang til Unity-editoren (scener, objekter, assets, konsol).

1. Installer `uv` (kræves af serveren): `brew install uv`
2. Unity Hub: opret et nyt 3D-projekt (URP).
3. Window → Package Manager → + → "Add package from git URL":
   `https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#v10.3.0`
4. Samme sted, "Add package by name": `com.unity.cloud.gltfast` (import af `.glb`).
5. Window → MCP for Unity → "Configure All Detected Clients" (finder Claude Code).
6. Genstart Claude Code, så MCP-værktøjerne indlæses.
7. Træk `.glb`-filerne fra en run (`runs/<run>/output/model/`) ind i projektets `Assets/`. 1 Unity-enhed = 1 m. Origo og akser står i `model.json`.

**Første scene (2026-11-03):** `Assets/Scenes/Analyzer3D.unity` i "My project". Filerne fra run `2026-11-03_1736_bfe-5522814` ligger i `Assets/Analyzer/<run>/`. Roden "Ejendom BFE 5522814" holder de fire modeller; `terraen` og `bygninger` er tændt, `terraen_klasser` og `tag_maalt` slukket. Skærmbilleder i `Assets/Screenshots/`.

**Faldgrube:** glTFast spejler x-aksen ved import (glTF er højrehåndet, Unity venstrehåndet). Uden rettelse peger øst mod −X og nord mod −Z. Roden er derfor drejet 180° om Y, så +X = øst og +Z = nord.

### Installeret software (Ricos Mac, 2026-11-03)

| Program | Version | Installeret med | Bruges til |
|---|---|---|---|
| uv | 0.13.7 | Homebrew (`brew install uv`), `/opt/homebrew/bin/uv` | Kører MCP for Unity-serveren |
| Unity Hub | – | Download fra unity.com, `/Applications/Unity Hub.app` | Styrer Unity-editorer og -projekter |
| Unity Editor | 6000.6.8f1 (også 6000.6.7f1 installeret) | Unity Hub | Editor til projektet |
| Unity-projekt "My project" | URP 17.6.0 | Unity Hub, `/Users/ricosorensen/My project` | Visning af 3D-modellen |
| MCP for Unity (`com.coplaydev.unity-mcp`) | fra `#main` | Package Manager, git-URL | Forbinder Claude Code med editoren. **Bør fastlåses til `#v10.3.0`** (manifest.json) |
| glTFast (`com.unity.cloud.gltfast`) | 6.20.0 | Package Manager (via Claude Code/MCP) | Import af `.glb` |

Claude Code: MCP-serveren `UnityMCP` (HTTP, `http://127.0.0.1:8080/mcp`) er registreret i `~/.claude.json` for både `My project` og `3xd-analyzer` (lokal scope, `claude mcp add --transport http UnityMCP http://127.0.0.1:8080/mcp`). Serveren startes fra Unity (Window → MCP for Unity) og skal køre, før Claude Code kan bruge den.

## Sidegevinst: ændringer over tid

Årgangene er nok mere værd til at se ændringer over tid end til 3D: ny belægning, fjernet beplantning, nye bygninger. Det kan bruges direkte i drift og som kontrol af klassificeringen.
