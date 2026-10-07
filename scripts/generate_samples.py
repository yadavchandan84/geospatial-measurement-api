"""Generate sample upload files in ./samples for trying the API by hand.

Usage:  python scripts/generate_samples.py
"""

import shutil
import tempfile
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, MultiPolygon, box

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
UTM33N = "EPSG:32633"


def write_zipped_shapefile(gdf: gpd.GeoDataFrame, layer: str, out_zip: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="'crs' was not provided")
            gdf.to_file(Path(tmp) / f"{layer}.shp", engine="pyogrio")
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for part in sorted(Path(tmp).iterdir()):
                zf.write(part, arcname=part.name)
    print(f"wrote {out_zip.relative_to(ROOT)}")


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)

    square = box(500000, 4980000, 501000, 4981000)  # 1 km x 1 km in UTM 33N
    parcels = gpd.GeoDataFrame(
        {"name": ["square_1km", "two_squares", "boundary_line"]},
        geometry=[
            square,
            MultiPolygon([box(502000, 4980000, 503000, 4981000), box(504000, 4980000, 505000, 4981000)]),
            LineString([(500000, 4979000), (501000, 4979000)]),
        ],
        crs=UTM33N,
    )
    # Shapefiles hold one geometry type per file, so lines go in their own layer.
    write_zipped_shapefile(parcels.iloc[:2], "parcels", SAMPLES / "parcels_utm33n.zip")
    write_zipped_shapefile(parcels.iloc[2:], "boundary", SAMPLES / "boundary_line_utm33n.zip")

    # Same square but without a .prj file -> measurements come back "unsupported".
    no_crs = gpd.GeoDataFrame({"name": ["square_no_crs"]}, geometry=[square], crs=None)
    write_zipped_shapefile(no_crs, "no_crs", SAMPLES / "square_missing_crs.zip")

    for kml in ("square_1km.kml", "mixed_geometries.kml"):
        shutil.copy(ROOT / "tests" / "fixtures" / kml, SAMPLES / kml)
        print(f"wrote samples/{kml}")


if __name__ == "__main__":
    main()
