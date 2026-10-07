"""Analysis grid in EPSG:25832 (ETRS89 / UTM 32N)."""

from dataclasses import dataclass

from pyproj import Transformer
from rasterio.transform import from_origin

CRS = "EPSG:25832"

_to_wgs84 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)


@dataclass(frozen=True)
class Grid:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    res: float

    @classmethod
    def around(cls, x: float, y: float, size: float, res: float) -> "Grid":
        half = size / 2
        return cls(x - half, y - half, x + half, y + half, res)

    @classmethod
    def from_bounds(cls, bounds, res: float, pad: float = 10.0) -> "Grid":
        xmin, ymin, xmax, ymax = bounds
        return cls(xmin - pad, ymin - pad, xmax + pad, ymax + pad, res)

    @property
    def width(self) -> int:
        return round((self.xmax - self.xmin) / self.res)

    @property
    def height(self) -> int:
        return round((self.ymax - self.ymin) / self.res)

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def transform(self):
        return from_origin(self.xmin, self.ymax, self.res, self.res)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.xmin, self.ymin, self.xmax, self.ymax

    def bbox_wgs84(self) -> tuple[float, float, float, float]:
        lon0, lat0 = _to_wgs84.transform(self.xmin, self.ymin)
        lon1, lat1 = _to_wgs84.transform(self.xmax, self.ymax)
        return lon0, lat0, lon1, lat1


def to_wgs84(x: float, y: float) -> tuple[float, float]:
    return _to_wgs84.transform(x, y)
