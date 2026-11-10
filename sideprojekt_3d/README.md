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

## Opgaver

### Opgave 1: tagudhæng, facaden skal stå ved ydervæggen (første version v0.8.0, 2026-11-10)

**Bygget:** `estimate_overhang()` finder udhænget `d` pr. bygning, så omridset rykket `d` ind langs de frie kanter har BBR's bebyggede areal (`byg041`, målt ved ydervæggen). Vægge, der deles med en nabobygning eller en gennemgang, bliver stående. Loft 0,8 m, under 0,15 m bliver til 0. Åbne bygninger og omrids målt ved væg (GeoDanmark `maalestedBygning`) får intet udhæng. Uden brugbart BBR-areal: 0,4 m, dog 0 for småbygninger (BBR 9xx). `place_walls()` lægger vægomridset på 3D-omridset efter gennemgangene, og `_overhang_mesh()` tegner tag på det fulde omrids, stern (0,2 m) ved tagkanten, underside under udhænget og vægge ved vægomridset op til undersiden. Felterne `model_udhaeng_m` og `model_udhaeng_kilde` står i `bygninger.csv`.

**Tal:** Hjørring rækkehuse 0,25–0,65 m (punktskyens jordpunkter under udhænget: 0,3–0,6 m). Testhuset med vinkel: 0,60 m, men jordpunkterne siger 0,3–0,4 m; BBR tæller nok ikke en åben overdækning med. Ikke gjort endnu: udhæng pr. side (tagfod/gavl) og kontrol mod jordpunkter i punktskyen. Analysen med kilder står i noten fra 2026-11-10 (tekniske tegninger i kommunale arkiver har ingen API og kan kun bruges manuelt).

Velegnet til en subagent. Oprettet 2026-11-03.

**Problem:** GeoDanmark-omridset (`metode3D = "Tag"`) er målt ved tagkanten, dvs. med tagudhæng. BBR's `byg041BebyggetAreal` er målt ved ydervæggen. På testcasen er GeoDanmark 8–25 % større (fx 162,1 m² mod 133 m²). I `bygninger.glb` står facaderne derfor ved tagkanten, og bygningerne ser for brede ud.

**Mål:** et estimat af udhænget pr. bygning (i meter) og et vægomrids, så modellen får facader ved ydervæggen og et tag, der rager ud over dem.

**Mulige metoder, der skal afprøves og sammenlignes:**
1. **BBR-areal:** find det indadgående buffer `d`, så arealet af `omrids.buffer(-d)` = BBR's bebyggede areal. Simpelt og findes for alle bygninger med BBR. Svagt hvis BBR-arealet er forkert eller medtager udestue/carport.
2. **Punktskyen:** vægpunkter (klasse 6 under tagfladen) eller kanten, hvor tagpunkter slutter, set mod terrænpunkter tæt på facaden.
3. **Skråfotos:** facaden ses direkte i de fire skrå retninger. Kameraerne er kendte (`pers:*`, se `analyzer/sources/skraafoto.py`), så omridset og et kandidat-vægomrids kan tegnes ind og sammenlignes visuelt eller med kantdetektion. Det er her, skråfotos første gang kan bruges på modellen.
4. **Typiske værdier:** dansk byggeskik (fx 0,3–0,6 m for rækkehuse) som sanity check, ikke som metode.

**Data:** run `runs/2026-11-03_1752_bfe-5522814/`: `output/bygninger.csv` (`grundplan_m2`, `bebygget_areal_bbr_m2`), `output/bygninger.geojson`, skråfotos i `output/skraafoto/` og metadata i `run.json` (`skraafoto`). Punktsky i `cache/dhm/`.

**Leverance:** en kort vurdering af metoderne med tal pr. bygning på testcasen, en anbefaling og et forslag til ændring i `sideprojekt_3d/model3d.py` (fx felt `model_udhaeng_m` i `bygninger.csv` og vægge ved `omrids.buffer(-udhæng)`). Ingen ændring i `analyzer/` uden aftale.

### Opgave 2: skråfotos som tekstur på modellen (første version 2026-11-03)

**Lavet:** `bygninger_skraafoto.glb`. Sideprojektet henter selv rene udsnit af run'ens fem skråfotos (pipelinens egne JPG'er har ejendomsgrænsen tegnet ind og er skaleret ned). Hver trekant får det billede, der ser mest vinkelret på den (`skraafoto_min_vinkel`), og UV beregnes med kameraets kendte orientering (`project()` fra `analyzer/sources/skraafoto.py`). Teksturerne ligger som JPEG i filen og vises uden lys (`KHR_materials_unlit`), fordi fotoet allerede har lyset. På testcasen bruges alle fem billeder (lodret og fire skrå, 6. april 2025). En kontrol, hvor trekanterne er tegnet ind i billederne, viser, at tage og facader rammer præcist.

**Mangler:**
- Okklusion: et træ eller en nabo foran en facade males på facaden. Kan løses ved at teste synlighed mod DHM overflade (stråle fra kamera til trekant).
- Facaderne står ved tagkanten (opgave 1), så facadefotoet trækkes lidt skævt ud ved udhænget.
- Terrænet har stadig ortofoto-farver pr. vertex, ikke skråfoto-tekstur.

### Opgave 3: træer i 3D (første version 2026-11-03)

**Lavet:** to lag, der vises som alternativer.
- `traeer.glb` (simpel model): stamme og ellipseformet krone pr. træ. Træerne findes på to måder. GeoDanmarks `Trae`-punkter giver stammens position; punktets z er terrænet ved stammen, og der er hverken højde, kronebredde eller art. Toppe i kronehøjdemodellen (DHM overflade minus terræn, let udglattet) inden for klassen Træer tilføjer de træer, GeoDanmark mangler. En top inden for `trae_top_radius_m` af et GeoDanmark-træ hører til det træ. Højden er toppen i kronehøjdemodellen. Kroneradius er medianen over 16 retninger ud til det sted, hvor kronen falder under `trae_kronekant_andel` af højden eller stiger mod en nabokrone. Kronebunden er antaget (`trae_kronebund_andel`), fordi overflademodellen ikke ser under kronen. Farven er medianen af ortofotoet i kronen. Data pr. træ ligger som glTF-extras (klik i `model.html`).
- `traeer_maalt.glb` (målt): punktskyens vegetationspunkter (ASPRS 4 og 5) på ejendommen som glTF-punkter, farvet fra ortofotoet.

**Testcase:** 33 træer, 16 fra GeoDanmark og 17 fra højdemodellen, plus 39.125 vegetationspunkter. Fem GeoDanmark-træer i gården har ingen målbar højde, så de små eller nyplantede træer får `trae_standard_hoejde_m`. En kontrol af kronerne tegnet på ortofotoet passer med træerne.

**Mangler:**
- Laserscanning og ortofoto er fra foråret, så løvtræer står uden blade, og kronefarven bliver brun eller grå.
- I sammenhængende bevoksning bliver kronerne for små, fordi der ikke er dal mellem kronerne. Der kan bruges watershed på kronehøjdemodellen.
- Kronebunden kan måles fra punktskyen (laveste vegetationspunkter under kronen).
- Unity: punkter vises som 1 px i glTFast. Træerne er ikke lagt i Unity-scenen endnu.

### Opgave 4: navngivne materialer pr. overflade (ikke startet, aftalt 2026-11-03)

Terrænet deles i ét mesh pr. arealtype. Belægningen deles yderligere efter de typer, `analyzer/paving.py` allerede finder. Materialerne får faste navne, som Unity og browseren kan erstatte med rigtige materialer (gentagne teksturer, fx fra ambientCG, CC0). Navnene i første omgang er `graes`, `buske`, `asfalt`, `fliser_sten_beton`, `grus` og `belaegning_ukendt`. Senere kan det blive et Unity Terrain med splatmap og græs som grass details.

## Klimadatastyrelsens 3D Ortofoto (3DO)

- Labs-prototype på Dataforsyningen: https://dataforsyningen.dk/labs/4963. Tredimensionelt luftfoto lavet ud fra skråfotos, hvor facader kan ses.
- Det kan være den færdige udgave af det, vi selv ville lave med splatting. Demoen viser dog Aarhus, ikke testcasen i Hjørring, og format, dækning, licens og adgang er ukendt.
- Beslutning 2026-11-03: vi bygger selv en model først. 3DO tages op igen, hvis KDS svarer, at det dækker landet og må bruges.

## Første skridt: bygningsmodel som baseline

**Tag pr. fløj (v0.7.0, 2026-11-10):** et L-, T- eller U-formet hus fik før ét tag over hele det omskrevne rektangel. Kippen kom forkert, og tilpasningen gjorde taget næsten fladt (testhus med vinkel: kip 0,4 m over tagfod, afvigelse 0,47 m). Nu deles et omrids, der fylder under 90 % af sit rektangel, i rektangulære fløje (`_wings()`). Hver fløj får sit eget sadel- eller valmtag tilpasset DHM, og kippen prøves både på langs og på tværs. En fløj, hvis kip løber ind i nabofløjen, forlænges til nabofløjens kiplinje. En fløj kan også få nabofløjens tagflade i et lige fald fra kippen (pulttag med høj side mod nabofløjen). Af de tre muligheder vælges den, der passer bedst til DHM. På testhuset fortsætter hovedtagets flade lige ned over vinkelfløjen; det bekræftede ejeren og DHM-profilet. Taget er det højeste af fløjenes tage, så der opstår skotrender, hvor de mødes. Små stykker (karnapper, indgangspartier under 6 m²) forlænger en fløjs gavl eller får fladt tag i tagfodshøjde. Testhuset: kip 1,4 m over tagfod, afvigelse 0,08 m. Rektangulære huse er uændrede. Tagfod tilpasset DHM må ligge ned til 1,0 m over terræn (`min_tagfod_maalt_m`, før 2,0 m), fordi terrænet er medianen rundt om bygningen: på skrånende grund står tagfoden på nedsiden lavere end medianen.

**Status 2026-11-03: bygget i v0.5.0** (`sideprojekt_3d/model3d.py`, output i run'ens `output/model/`). Testcasen: 8 bygninger. Tøndetagene afviger 0,22–0,39 m i snit fra DHM efter tilpasning med mindste kvadrater. To bygninger er usikre og får fladt tag: én dækket af træer, og en overdækning fra 2025 (bygget efter punktskyen), hvor højden kommer fra GeoDanmarks fotogrammetrisk målte tagkant (±0,15 m). GeoDanmarks tagkant bruges kun, når den er målt (ikke `Manuel`); ellers BBR-etager.

**Dobbelttjek 2026-11-03:** alle 8 GeoDanmark-bygninger ligger helt på ejendommen og har BBR (ingen delvist udeladt). Usikre tage (dækket af træer eller bygget efter skanningen) får ikke højde fra DHM, som ellers måler trækronerne: udhuset (nr. 7) gik fra 6,1 m til 3,0 m (BBR-etager). GeoDanmark-omridset er målt ved tagkanten, så det er 8–25 % større end BBR's bebyggede areal (ydervæg); facaderne i modellen står derfor ved tagkanten. Ikke tjekket: BBR-bygninger på ejendommen uden omrids i GeoDanmark (GraphQL kan kun slå op på id). Næste skridt: åbn `bygninger.glb` i Unity (glTFast) og sammenlign med CityWeft's gratis forhåndsvisning.

**Rettelse v0.7.1 (2026-11-10), overdækkede gennemgange:** ved hver gennemgang gennem rækkehusene stod en hvid trekant. Den fælles væg mellem to bygninger fik et par cm facade med i hver ende, så båndet, der skærer gennemgangen ud, endte skråt. Det skar en trekant (ca. 2,2 m²) ud af gennemgangen, og den blev stående som et lille stykke af nabobygningen inde i gennemgangen. Nu skæres gennemgangen ud langs en ret linje mellem væggens ender, og båndet går ud over facaderne, så gennemgangen og husene på hver side slutter tæt.

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
