"""Geodesic measurement of shapely geometries.

All geometries passed here must already be in EPSG:4326 (lon/lat). Measuring on
the WGS84 ellipsoid with pyproj.Geod gives accurate metres / square metres
anywhere on Earth, without having to pick a projected CRS per feature.
"""

from dataclasses import dataclass

import shapely
from pyproj import Geod
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

GEOD = Geod(ellps="WGS84")

MEASURED = "measured"
UNSUPPORTED = "unsupported"


@dataclass
class MeasurementResult:
    status: str
    reason: str | None = None
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    is_valid: bool | None = None
    repaired: bool = False


def unsupported(reason: str, is_valid: bool | None = None) -> MeasurementResult:
    return MeasurementResult(status=UNSUPPORTED, reason=reason, is_valid=is_valid)


def _ring_length(coords) -> float:
    lons, lats = zip(*[(c[0], c[1]) for c in coords])
    return GEOD.line_length(lons, lats)


def _polygon_area_perimeter(poly: Polygon) -> tuple[float, float]:
    """Area = |exterior| - sum(|holes|). Perimeter includes hole boundaries."""
    ext_area, ext_perim = GEOD.polygon_area_perimeter(*poly.exterior.coords.xy)
    area = abs(ext_area)
    perimeter = ext_perim
    for interior in poly.interiors:
        hole_area, hole_perim = GEOD.polygon_area_perimeter(*interior.coords.xy)
        area -= abs(hole_area)
        perimeter += hole_perim
    return area, perimeter


def _accumulate(geom: BaseGeometry, totals: dict) -> None:
    """Recursively add area/perimeter/length of every measurable part."""
    if geom.is_empty:
        return
    if isinstance(geom, Polygon):
        area, perim = _polygon_area_perimeter(geom)
        totals["area"] += area
        totals["perimeter"] += perim
        totals["polygonal"] = True
    elif isinstance(geom, (LineString, LinearRing)):
        totals["length"] += _ring_length(geom.coords)
        totals["linear"] = True
    elif isinstance(geom, (MultiPolygon, MultiLineString, GeometryCollection)):
        for part in geom.geoms:
            _accumulate(part, totals)
    elif isinstance(geom, (Point, MultiPoint)):
        totals["points"] = True


def _repair_if_needed(geom: BaseGeometry) -> tuple[BaseGeometry, bool, bool]:
    """Return (geometry, was_valid, repaired).

    Invalid polygons (e.g. self-intersecting "bow-ties") give meaningless areas,
    so they are repaired with shapely.make_valid before measuring.
    """
    if geom.is_valid:
        return geom, True, False
    fixed = shapely.make_valid(geom)
    return fixed, False, True


def measure_geometry(geom: BaseGeometry | None) -> MeasurementResult:
    """Measure a WGS84 geometry. Never raises for bad input; returns 'unsupported'."""
    if geom is None:
        return unsupported("Feature has no geometry (null).")
    if geom.is_empty:
        return unsupported("Geometry is empty.")

    try:
        geom = shapely.force_2d(geom)
        geom, was_valid, repaired = _repair_if_needed(geom)

        totals = {
            "area": 0.0,
            "perimeter": 0.0,
            "length": 0.0,
            "polygonal": False,
            "linear": False,
            "points": False,
        }
        _accumulate(geom, totals)
    except Exception as exc:  # defensive: malformed coordinates etc.
        return unsupported(f"Geometry could not be measured: {exc}")

    if not totals["polygonal"] and not totals["linear"]:
        if totals["points"]:
            return unsupported(
                "Point geometries have no area or length.", is_valid=was_valid
            )
        return unsupported("Geometry has no measurable parts.", is_valid=was_valid)

    return MeasurementResult(
        status=MEASURED,
        area_m2=totals["area"] if totals["polygonal"] else None,
        perimeter_m=totals["perimeter"] if totals["polygonal"] else None,
        length_m=totals["length"] if totals["linear"] else None,
        is_valid=was_valid,
        repaired=repaired,
    )
