"""End-to-end tests for zipped shapefile uploads."""

import pytest
from shapely.geometry import LineString, MultiPolygon, Polygon, box

from tests.conftest import SQUARE_1KM_UTM


def _measurements(client, file_id):
    resp = client.get(f"/api/files/{file_id}/measurements/")
    assert resp.status_code == 200
    return resp.json()


def test_projected_shapefile_square(client, upload, make_shapefile_zip):
    resp = upload("parcels.zip", make_shapefile_zip([SQUARE_1KM_UTM], names=["square"]))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["file_type"] == "shapefile"
    assert body["source_crs"] == "EPSG:32633"
    # Stored geometry is normalised to lon/lat.
    lon, lat = body["features"][0]["geometry"]["coordinates"][0][0]
    assert 14 < lon < 16 and 44 < lat < 46

    data = _measurements(client, body["id"])
    assert data["source_crs"] == "EPSG:32633"
    assert data["features"][0]["area_m2"] == pytest.approx(1_000_000, rel=5e-3)
    assert data["features"][0]["perimeter_m"] == pytest.approx(4_000, rel=5e-3)


def test_geographic_shapefile(client, upload, make_shapefile_zip):
    # ~0.01 deg box near the equator; compare against a known geodesic value.
    resp = upload("wgs.zip", make_shapefile_zip([box(0, 0, 0.01, 0.01)], crs="EPSG:4326"))
    assert resp.status_code == 201, resp.text
    area = _measurements(client, resp.json()["id"])["features"][0]["area_m2"]
    # 0.01 deg ~ 1113.2 m (lon) x 1105.7 m (lat) at the equator.
    assert area == pytest.approx(1_230_850, rel=5e-3)


def test_multipolygon_shapefile(client, upload, make_shapefile_zip):
    multi = MultiPolygon([SQUARE_1KM_UTM, box(502000, 4980000, 503000, 4981000)])
    resp = upload("multi.zip", make_shapefile_zip([multi]))
    assert resp.status_code == 201, resp.text
    feature = _measurements(client, resp.json()["id"])["features"][0]
    assert feature["geometry_type"] == "MultiPolygon"
    assert feature["area_m2"] == pytest.approx(2_000_000, rel=5e-3)


def test_line_shapefile(client, upload, make_shapefile_zip):
    line = LineString([(500000, 4980000), (501000, 4980000), (501000, 4981000)])
    resp = upload("roads.zip", make_shapefile_zip([line], layer="roads"))
    assert resp.status_code == 201, resp.text
    feature = _measurements(client, resp.json()["id"])["features"][0]
    assert feature["length_m"] == pytest.approx(2_000, rel=5e-3)
    assert feature["layer"] == "roads"


def test_null_and_empty_geometries_are_unsupported(client, upload, make_shapefile_zip):
    resp = upload(
        "nulls.zip",
        make_shapefile_zip([SQUARE_1KM_UTM, None, Polygon()], names=["ok", "null", "empty"]),
    )
    assert resp.status_code == 201, resp.text
    data = _measurements(client, resp.json()["id"])
    by_name = {f["name"]: f for f in data["features"]}
    assert by_name["ok"]["status"] == "measured"
    assert by_name["null"]["status"] == "unsupported"
    assert by_name["empty"]["status"] == "unsupported"
    assert data["totals"]["measured_features"] == 1
    assert data["totals"]["area_m2"] == pytest.approx(1_000_000, rel=5e-3)


def test_missing_crs_returns_unsupported_not_error(client, upload, make_shapefile_zip):
    resp = upload("no_prj.zip", make_shapefile_zip([SQUARE_1KM_UTM], crs=None))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_crs"] is None
    assert any("no CRS" in w for w in body["warnings"])

    data = _measurements(client, body["id"])
    feature = data["features"][0]
    assert feature["status"] == "unsupported"
    assert "CRS" in feature["reason"]
    assert feature["area_m2"] is None
    assert data["totals"]["area_m2"] == 0


def test_shapefile_inside_subfolder(client, upload, make_shapefile_zip):
    resp = upload("nested.zip", make_shapefile_zip([SQUARE_1KM_UTM], folder="data/parcels/"))
    assert resp.status_code == 201, resp.text


def test_uppercase_extension_is_accepted(upload, make_shapefile_zip):
    assert upload("PARCELS.ZIP", make_shapefile_zip([SQUARE_1KM_UTM])).status_code == 201
