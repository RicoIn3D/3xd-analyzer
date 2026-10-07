---
projekt: Analyzier
emne: run-oversigt
---

# Run-oversigt

Én linje pr. run. Kvalitetsmål: mindst **85 %** korrekt belægning.

Score-kilde: `R` = Ricos vurdering (`evaluering.md`), `F` = automatisk mod facit (`metrics.json`).

| Run | BFE | Metode / version | Kilder | Belægning % | Græs % | Træer/buske % | Samlet % | Score-kilde | Dom | Mål nået |
|---|---|---|---|---|---|---|---|---|---|---|
| [2026-10-07_1153_bfe-5522814](2026-10-07_1153_bfe-5522814/viewer.html) | 5522814 | sentinel2 v0.1.0 | Sentinel-2 L2A, ESA WorldCover 2021 |  |  | |  |  | forkastet: OSM timeout, mappenavn i UTC | |
| [2026-10-07_1355_bfe-5522814](2026-10-07_1355_bfe-5522814/viewer.html) | 5522814 | sentinel2 v0.1.0 | OpenStreetMap, Sentinel-2 L2A, ESA WorldCover 2021 |  |  | |  |  | ugyldig: ikke afgrænset til matriklen (400x400 m firkant) | |
| [2026-10-07_1401_bfe-5522814](2026-10-07_1401_bfe-5522814/viewer.html) | 5522814 | sentinel2 v0.1.0 | OpenStreetMap, Sentinel-2 L2A, ESA WorldCover 2021 |  |  | |  |  | ugyldig: ikke afgrænset til matriklen (400x400 m firkant) | |
| [2026-10-07_1405_bfe-5522814](2026-10-07_1405_bfe-5522814/viewer.html) | 5522814 | sentinel2 v0.1.0 | Datafordeler MAT: samlet fast ejendom 5522814, GeoDanmark vektor, Sentinel-2 L2A, ESA WorldCover 2021 |  |  | |  |  | erstattet: OSM timeout, uden ortofoto | |
| [2026-10-07_1409_bfe-5522814](2026-10-07_1409_bfe-5522814/viewer.html) | 5522814 | sentinel2 v0.1.0 | Datafordeler MAT: samlet fast ejendom 5522814, OpenStreetMap, GeoDanmark vektor, Sentinel-2 L2A, ESA WorldCover 2021 |  |  | |  |  | erstattet: uden ortofoto (kun 10 m data) | |
| [2026-10-07_1414_bfe-5522814](2026-10-07_1414_bfe-5522814/viewer.html) | 5522814 | ortho_dhm v0.1.0 | Datafordeler MAT: samlet fast ejendom 5522814, GeoDanmark vektor, Ortofoto 2025 RGB, Ortofoto 2024 RGB, Ortofoto 2023 RGB, Ortofoto 2025 CIR/nær-infrarød, DHM terræn, DHM overflade, Sentinel-2 L2A, ESA WorldCover 2021 |  |  | |  |  | erstattet: fejl i v0.1 (nøgne træer på forårsfoto blev til bygning) | |
| [2026-10-07_1419_bfe-5522814](2026-10-07_1419_bfe-5522814/viewer.html) | 5522814 | ortho_dhm v0.2.0 | Datafordeler MAT: samlet fast ejendom 5522814, GeoDanmark vektor, Ortofoto 2025 RGB, Ortofoto 2024 RGB, Ortofoto 2023 RGB, Ortofoto 2025 CIR/nær-infrarød, DHM terræn, DHM overflade, Sentinel-2 L2A, ESA WorldCover 2021 |  |  | |  |  | afventer | |
