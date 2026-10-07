"""Unit tests for the geodesic measurement service (no HTTP, no database)."""

import pytest
from pyproj import Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.ops import transform

from app.services.measurement import MEASURED, UNSUPPORTED, measure_geometry

_to_wgs84 = Transformer.from_crs("EPSG:32633", "EPSG:4326", always_xy=True).transform


def utm_to_wgs84(geom):
    return transform(_to_wgs84, geom)


SQUARE = utm_to_wgs84(box(500000, 4980000, 501000, 4981000))  # 1 km x 1 km


def test_one_km_square_is_about_one_million_square_metres():
    result = measure_geometry(SQUARE)
    assert result.status == MEASURED
    # UTM's 0.9996 scale factor makes the true area ~0.08% larger than 1e6.
    assert result.area_m2 == pytest.approx(1_000_000, rel=5e-3)
    assert result.perimeter_m == pytest.approx(4_000, rel=5e-3)
    assert result.length_m is None


def test_polygon_with_hole_subtracts_hole_area():
    outer = box(500000, 4980000, 501000, 4981000)
    hole = box(500250, 4980250, 500750, 4980750)  # 500 m x 500 m
    poly = utm_to_wgs84(Polygon(outer.exterior.coords, [hole.exterior.coords]))
    result = measure_geometry(poly)
    assert result.area_m2 == pytest.approx(750_000, rel=5e-3)
    assert result.perimeter_m == pytest.approx(4_000 + 2_000, rel=5e-3)


def test_multipolygon_sums_parts():
    second = utm_to_wgs84(box(502000, 4980000, 503000, 4981000))
    result = measure_geometry(MultiPolygon([SQUARE, second]))
    assert result.status == MEASURED
    assert result.area_m2 == pytest.approx(2_000_000, rel=5e-3)


def test_linestring_length():
    line = utm_to_wgs84(LineString([(500000, 4980000), (501000, 4980000)]))
    result = measure_geometry(line)
    assert result.status == MEASURED
    assert result.length_m == pytest.approx(1_000, rel=5e-3)
    assert result.area_m2 is None


def test_counterclockwise_and_clockwise_give_same_positive_area():
    reversed_square = Polygon(list(SQUARE.exterior.coords)[::-1])
    assert measure_geometry(reversed_square).area_m2 == pytest.approx(
        measure_geometry(SQUARE).area_m2
    )


def test_3d_coordinates_are_ignored():
    square_z = Polygon([(x, y, 123.0) for x, y in SQUARE.exterior.coords])
    assert measure_geometry(square_z).area_m2 == pytest.approx(measure_geometry(SQUARE).area_m2)


def test_invalid_bowtie_is_repaired():
    # Self-intersecting "bow-tie": naive signed area would cancel out to ~0.
    xs = [p[0] for p in SQUARE.exterior.coords]
    ys = [p[1] for p in SQUARE.exterior.coords]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bowtie = Polygon([(x0, y0), (x1, y1), (x1, y0), (x0, y1), (x0, y0)])
    result = measure_geometry(bowtie)
    assert result.status == MEASURED
    assert result.is_valid is False
    assert result.repaired is True
    # Two triangles, each a quarter of the square.
    assert result.area_m2 == pytest.approx(500_000, rel=1e-2)


def test_geometry_collection_measures_each_part():
    line = utm_to_wgs84(LineString([(500000, 4980000), (501000, 4980000)]))
    result = measure_geometry(GeometryCollection([SQUARE, line, Point(15, 45)]))
    assert result.status == MEASURED
    assert result.area_m2 == pytest.approx(1_000_000, rel=5e-3)
    assert result.length_m == pytest.approx(1_000, rel=5e-3)


@pytest.mark.parametrize(
    "geom, reason_fragment",
    [
        (None, "no geometry"),
        (Polygon(), "empty"),
        (Point(15, 45), "Point"),
        (GeometryCollection(), "empty"),
    ],
)
def test_unmeasurable_geometries_are_unsupported_not_errors(geom, reason_fragment):
    result = measure_geometry(geom)
    assert result.status == UNSUPPORTED
    assert reason_fragment.lower() in result.reason.lower()
    assert result.area_m2 is None and result.length_m is None
