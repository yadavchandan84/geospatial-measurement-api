"""End-to-end tests for KML uploads."""

import pytest


def test_upload_square_kml_and_measure(client, upload, fixture_bytes):
    resp = upload("square_1km.kml", fixture_bytes("square_1km.kml"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["file_type"] == "kml"
    assert body["source_crs"] == "EPSG:4326"
    assert body["feature_count"] == 1
    assert body["features"][0]["name"] == "Square 1km"
    assert body["features"][0]["geometry"]["type"] == "Polygon"

    file_id = body["id"]
    detail = client.get(f"/api/files/{file_id}/")
    assert detail.status_code == 200
    assert detail.json()["id"] == file_id

    m = client.get(f"/api/files/{file_id}/measurements/")
    assert m.status_code == 200
    data = m.json()
    assert data["measurement_crs"] == "EPSG:4326"
    feature = data["features"][0]
    assert feature["status"] == "measured"
    assert feature["area_m2"] == pytest.approx(1_000_000, rel=5e-3)
    assert feature["area_km2"] == pytest.approx(1.0, rel=5e-3)
    assert data["totals"]["area_m2"] == pytest.approx(1_000_000, rel=5e-3)


def test_mixed_kml_handles_multi_null_and_points(client, upload, fixture_bytes):
    resp = upload("mixed_geometries.kml", fixture_bytes("mixed_geometries.kml"))
    assert resp.status_code == 201, resp.text
    file_id = resp.json()["id"]

    data = client.get(f"/api/files/{file_id}/measurements/").json()
    by_name = {f["name"]: f for f in data["features"]}
    assert set(by_name) == {"Two squares", "Square with hole", "Line 1km", "A point", "No geometry"}

    assert by_name["Two squares"]["geometry_type"] == "MultiPolygon"
    assert by_name["Two squares"]["area_m2"] == pytest.approx(2_000_000, rel=5e-3)
    assert by_name["Square with hole"]["area_m2"] == pytest.approx(750_000, rel=5e-3)
    assert by_name["Line 1km"]["length_m"] == pytest.approx(1_000, rel=5e-3)

    assert by_name["A point"]["status"] == "unsupported"
    assert by_name["No geometry"]["status"] == "unsupported"
    assert by_name["No geometry"]["geometry_type"] is None
    assert "no geometry" in by_name["No geometry"]["reason"].lower()

    totals = data["totals"]
    assert totals["measured_features"] == 3
    assert totals["unsupported_features"] == 2
    assert totals["area_m2"] == pytest.approx(2_750_000, rel=5e-3)
    assert totals["length_m"] == pytest.approx(1_000, rel=5e-3)


def test_get_file_without_geometry(client, upload, fixture_bytes):
    file_id = upload("square_1km.kml", fixture_bytes("square_1km.kml")).json()["id"]
    body = client.get(f"/api/files/{file_id}/", params={"include_geometry": False}).json()
    assert body["features"][0]["geometry"] is None


def test_non_kml_content_with_kml_extension_is_rejected(upload):
    resp = upload("fake.kml", b"this is not xml at all")
    assert resp.status_code == 422
    assert "KML" in resp.json()["detail"]


def test_malformed_kml_is_rejected_gracefully(upload):
    resp = upload("broken.kml", b"<?xml version='1.0'?><kml><Document><Placemark>")
    assert resp.status_code in (201, 422)  # never a 500
