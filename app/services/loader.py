"""Read KML / shapefiles into GeoDataFrames using geopandas + pyogrio (GDAL)."""

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import pyogrio

from app.services.validation import UploadError

WGS84 = "EPSG:4326"


@dataclass
class LoadedLayer:
    name: str
    gdf: gpd.GeoDataFrame
    crs_assumed: bool = False


def _read(path: Path, layer: str | None = None) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(path, layer=layer, engine="pyogrio")
    except Exception as exc:  # pyogrio raises several DataSource/Field errors
        raise UploadError(f"Could not read geospatial data: {exc}", status_code=422) from exc


def load_kml(path: Path) -> list[LoadedLayer]:
    """KML can hold several layers (one per Folder/Document); read all of them.

    The KML spec mandates WGS84 lon/lat, so a missing CRS is safely assumed to
    be EPSG:4326 (and flagged as assumed).
    """
    try:
        layer_info = pyogrio.list_layers(path)
    except Exception as exc:
        raise UploadError(f"Could not read KML file: {exc}", status_code=422) from exc

    layers: list[LoadedLayer] = []
    for name, _geom_type in layer_info:
        gdf = _read(path, layer=name)
        assumed = gdf.crs is None
        if assumed:
            gdf = gdf.set_crs(WGS84)
        layers.append(LoadedLayer(name=str(name), gdf=gdf, crs_assumed=assumed))

    # GDAL's KML driver silently drops Placemarks that have no geometry. Recover
    # them so they are reported as "unsupported (null geometry)" instead of
    # disappearing from the results.
    orphans = _placemarks_without_geometry(path)
    if orphans:
        gdf = gpd.GeoDataFrame(orphans, geometry=[None] * len(orphans), crs=WGS84)
        layers.append(LoadedLayer(name=NO_GEOMETRY_LAYER, gdf=gdf))
    return layers


NO_GEOMETRY_LAYER = "placemarks_without_geometry"
_KML_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry", "Model", "Track", "MultiTrack"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _placemarks_without_geometry(path: Path) -> list[dict]:
    # Python's expat parser does not resolve external entities, which keeps
    # this safe against XXE for untrusted uploads.
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError:
        return []

    rows = []
    for elem in root.iter():
        if _local(elem.tag) != "Placemark":
            continue
        children = {_local(c.tag): c for c in elem}
        has_geometry = any(_local(d.tag) in _KML_GEOMETRY_TAGS for d in elem.iter())
        if not has_geometry:
            name = children.get("name")
            desc = children.get("description")
            rows.append(
                {
                    "Name": (name.text or "").strip() if name is not None else None,
                    "Description": (desc.text or "").strip() if desc is not None else None,
                }
            )
    return rows


def load_shapefile(shp_path: Path, layer_name: str) -> list[LoadedLayer]:
    """Read a shapefile. A missing .prj leaves gdf.crs as None; we do NOT guess."""
    gdf = _read(shp_path)
    return [LoadedLayer(name=layer_name, gdf=gdf, crs_assumed=False)]
