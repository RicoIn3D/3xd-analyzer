"""Area classes. Values are stored in klasser.tif; 0 means outside the analysis area."""

UDENFOR = 0
BELAEGNING = 1
GRAES = 2
BUSKE = 3
TRAEER = 4
BYGNING = 5
UKENDT = 6

NAMES = {
    BELAEGNING: "Belægning",
    GRAES: "Græs",
    BUSKE: "Buske",
    TRAEER: "Træer",
    BYGNING: "Bygning",
    UKENDT: "Ukendt",
}

COLORS = {
    BELAEGNING: (158, 158, 158),
    GRAES: (139, 205, 80),
    BUSKE: (46, 139, 87),
    TRAEER: (16, 82, 32),
    BYGNING: (200, 70, 60),
    UKENDT: (240, 200, 40),
}

# Classes that count as outdoor area (buildings are excluded)
OUTDOOR = (BELAEGNING, GRAES, BUSKE, TRAEER, UKENDT)
