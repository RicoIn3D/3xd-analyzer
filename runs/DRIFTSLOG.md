# Driftslog

Problemer med datakilder under runs. Pipelinen skriver automatisk nye linjer her.

| Tidspunkt | Run | Kilde | Problem |
|---|---|---|---|
| 2026-10-07 11:53 | 2026-10-07_1153_bfe-5522814 | Overpass | overpass-api.de HTTP 504. Run forkastet |
| 2026-10-07 14:05 | 2026-10-07_1405_bfe-5522814 | Overpass | Alle tre servere fejlede: overpass-api.de HTTP 504, overpass.kumi.systems ReadTimeout, overpass.private.coffee HTTP 504 |
| 2026-10-07 14:14 | 2026-10-07_1414_bfe-5522814 | Overpass | Samme som ovenfor |
| 2026-10-07 14:19 | 2026-10-07_1419_bfe-5522814 | Overpass | Samme som ovenfor |
| 2026-10-07 14:30 | (test af retry) | Overpass | Runde 1: alle tre fejlede (504, ReadTimeout, ReadTimeout). Runde 2: overpass-api.de svarede. Ca. 205 sekunder i alt. Svaret er nu i cache |
