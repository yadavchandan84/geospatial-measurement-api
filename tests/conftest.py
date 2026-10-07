"""Shared pytest fixtures.

KML fixtures are static files in tests/fixtures/. Shapefile fixtures are built
on the fly with geopandas because shapefiles are binary multi-file formats;
building them in code keeps the expected geometry readable in the test itself.
"""

import os
import shutil
import tempfile
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

# Point the app at a throwaway SQLite database *before* importing it.
_TMP_DB_DIR = tempfile.mkdtemp(prefix="geoapi_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP_DB_DIR, 'test.db').as_posix()}"

import geopandas as gpd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from shapely.geometry import box  # noqa: E402

from app.main import app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

# A 1000 m x 1000 m square in UTM zone 33N (metres).
UTM33N = "EPSG:32633"
SQUARE_1KM_UTM = box(500000, 4980000, 501000, 4981000)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(_TMP_DB_DIR, ignore_errors=True)


@pytest.fixture
def fixture_bytes() -> Callable[[str], bytes]:
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def make_shapefile_zip(tmp_path) -> Callable[..., bytes]:
    """Build a zipped shapefile from geometries and return the zip bytes.

    drop: shapefile extensions to leave out of the zip (e.g. [".shx"]).
    crs=None writes no .prj file, simulating a shapefile with a missing CRS.
    """

    def _make(
        geometries,
        crs: str | None = UTM33N,
        names: list[str] | None = None,
        drop: tuple[str, ...] = (),
        folder: str = "",
        layer: str = "parcels",
    ) -> bytes:
        names = names or [f"feature {i}" for i in range(len(geometries))]
        gdf = gpd.GeoDataFrame({"name": names}, geometry=list(geometries), crs=crs)

        out_dir = tmp_path / f"shp_{layer}_{len(list(tmp_path.iterdir()))}"
        out_dir.mkdir()
        with warnings.catch_warnings():
            # Writing without a CRS is intentional for the "missing .prj" case.
            warnings.filterwarnings("ignore", message="'crs' was not provided")
            gdf.to_file(out_dir / f"{layer}.shp", engine="pyogrio")

        zip_path = out_dir / "upload.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for part in out_dir.iterdir():
                if part.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg"} and part.suffix.lower() not in drop:
                    zf.write(part, arcname=f"{folder}{part.name}")
        return zip_path.read_bytes()

    return _make


@pytest.fixture
def upload(client: TestClient):
    """POST a file to the upload endpoint and return the response."""

    def _upload(filename: str, content: bytes):
        return client.post(
            "/api/files/", files={"file": (filename, content, "application/octet-stream")}
        )

    return _upload
