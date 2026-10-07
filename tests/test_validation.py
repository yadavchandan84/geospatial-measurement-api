"""Upload validation: extensions, zip structure, size limits, unknown ids."""

import io
import zipfile

import pytest

from app.config import Settings, get_settings
from app.main import app
from tests.conftest import SQUARE_1KM_UTM


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize("filename", ["data.geojson", "data.shp", "notes.txt", "noextension"])
def test_unsupported_extensions_are_rejected(upload, filename):
    resp = upload(filename, b"whatever")
    assert resp.status_code == 415
    assert "Unsupported file extension" in resp.json()["detail"]


def test_empty_file_is_rejected(upload):
    resp = upload("empty.kml", b"")
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_zip_that_is_not_a_zip(upload):
    resp = upload("fake.zip", b"definitely not a zip")
    assert resp.status_code == 400
    assert "not a valid zip" in resp.json()["detail"]


def test_zip_without_shp(upload):
    resp = upload("docs.zip", _zip({"readme.txt": b"hello"}))
    assert resp.status_code == 400
    assert ".shp" in resp.json()["detail"]


@pytest.mark.parametrize("missing", [".shx", ".dbf"])
def test_zip_missing_required_component(upload, make_shapefile_zip, missing):
    resp = upload("partial.zip", make_shapefile_zip([SQUARE_1KM_UTM], drop=(missing,)))
    assert resp.status_code == 400
    assert missing in resp.json()["detail"]


def test_zip_with_two_shapefiles(upload):
    entries = {f"{stem}{ext}": b"x" for stem in ("a", "b") for ext in (".shp", ".shx", ".dbf")}
    resp = upload("two.zip", _zip(entries))
    assert resp.status_code == 400
    assert "more than one shapefile" in resp.json()["detail"]


def test_zip_slip_path_is_rejected(upload):
    entries = {"../evil.shp": b"x", "../evil.shx": b"x", "../evil.dbf": b"x"}
    resp = upload("evil.zip", _zip(entries))
    assert resp.status_code == 400
    assert "unsafe path" in resp.json()["detail"]


def test_corrupt_shapefile_contents_give_422(upload):
    entries = {"bad.shp": b"garbage", "bad.shx": b"garbage", "bad.dbf": b"garbage"}
    resp = upload("bad.zip", _zip(entries))
    assert resp.status_code == 422


def test_upload_size_limit(upload, fixture_bytes):
    app.dependency_overrides[get_settings] = lambda: Settings(max_upload_mb=0)
    try:
        resp = upload("square_1km.kml", fixture_bytes("square_1km.kml"))
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 413


def test_unknown_file_id_returns_404(client):
    assert client.get("/api/files/does-not-exist/").status_code == 404
    assert client.get("/api/files/does-not-exist/measurements/").status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
