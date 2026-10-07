"""Upload pipeline: validate -> read -> normalise CRS -> measure -> persist."""

import json
import math
import tempfile
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pandas as pd
import shapely
from pyproj import CRS
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Feature, UploadedFile
from app.services.loader import WGS84, LoadedLayer, load_kml, load_shapefile
from app.services.measurement import MeasurementResult, measure_geometry, unsupported
from app.services.validation import (
    UploadError,
    detect_file_type,
    extract_shapefile,
    inspect_shapefile_zip,
    validate_kml_bytes,
)

MISSING_CRS_REASON = (
    "Source CRS is missing (no .prj file); coordinates cannot be converted to "
    "real-world units, so measurement was skipped."
)


def crs_label(crs: CRS | None) -> str | None:
    if crs is None:
        return None
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy values to JSON-serialisable Python values."""
    if value is None:
        return None
    if isinstance(value, (np.generic,)):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _find_name_column(columns) -> str | None:
    for col in columns:
        if str(col).lower() == "name":
            return col
    return None


def _geojson(geom: BaseGeometry | None) -> str | None:
    if geom is None:
        return None
    try:
        return json.dumps(mapping(geom))
    except Exception:
        return None


def _coords_problem(geom: BaseGeometry) -> str | None:
    """Detect reprojection failures or a CRS that does not match the coordinates."""
    coords = shapely.get_coordinates(geom)
    if coords.size == 0:
        return None
    if not np.isfinite(coords).all():
        return "Reprojection to EPSG:4326 produced invalid (non-finite) coordinates."
    lon, lat = coords[:, 0], coords[:, 1]
    if (np.abs(lat) > 90).any() or (np.abs(lon) > 180.0 + 1e-9).any():
        return (
            "Coordinates fall outside the valid longitude/latitude range after "
            "reprojection; the declared CRS is probably wrong."
        )
    return None


def _build_features(layer: LoadedLayer, start_index: int) -> list[Feature]:
    gdf = layer.gdf
    crs = gdf.crs
    if crs is not None and not crs.equals(CRS.from_user_input(WGS84)):
        try:
            gdf_wgs = gdf.to_crs(WGS84)
        except Exception as exc:
            raise UploadError(f"Could not reproject from {crs_label(crs)}: {exc}", 422) from exc
    else:
        gdf_wgs = gdf

    name_col = _find_name_column(gdf.columns)
    geom_col = gdf.geometry.name
    prop_cols = [c for c in gdf.columns if c != geom_col]

    features: list[Feature] = []
    for i in range(len(gdf)):
        src_geom = gdf.geometry.iloc[i]
        wgs_geom = gdf_wgs.geometry.iloc[i]
        row = gdf.iloc[i]

        result: MeasurementResult
        if src_geom is None:
            result = unsupported("Feature has no geometry (null).")
        elif src_geom.is_empty:
            result = unsupported("Geometry is empty.")
        elif crs is None:
            result = unsupported(MISSING_CRS_REASON)
        elif problem := _coords_problem(wgs_geom):
            result = unsupported(problem)
        else:
            result = measure_geometry(wgs_geom)

        stored_geom = wgs_geom if crs is not None else src_geom
        name = _json_safe(row[name_col]) if name_col is not None else None

        features.append(
            Feature(
                feature_index=start_index + i,
                layer=layer.name,
                name=str(name)[:255] if name is not None else None,
                geometry_type=src_geom.geom_type if src_geom is not None else None,
                geometry_geojson=_geojson(stored_geom),
                properties={str(c): _json_safe(row[c]) for c in prop_cols},
                status=result.status,
                reason=result.reason,
                area_m2=result.area_m2,
                perimeter_m=result.perimeter_m,
                length_m=result.length_m,
                is_valid=result.is_valid,
                repaired=result.repaired,
            )
        )
    return features


def _load_layers(file_type: str, content: bytes, tmp: Path, settings: Settings) -> list[LoadedLayer]:
    if file_type == "kml":
        validate_kml_bytes(content)
        path = tmp / "upload.kml"
        path.write_bytes(content)
        return load_kml(path)

    zip_path = tmp / "upload.zip"
    zip_path.write_bytes(content)
    members = inspect_shapefile_zip(zip_path, settings)
    shp_path = extract_shapefile(zip_path, members, tmp / "extracted")
    return load_shapefile(shp_path, layer_name=PurePosixPath(members.shp).stem)


def ingest_upload(db: Session, filename: str | None, content: bytes, settings: Settings) -> UploadedFile:
    file_type = detect_file_type(filename)
    if not content:
        raise UploadError("Uploaded file is empty.")

    with tempfile.TemporaryDirectory(prefix="geoupload_") as tmp_dir:
        layers = _load_layers(file_type, content, Path(tmp_dir), settings)

        warnings: list[str] = []
        features: list[Feature] = []
        for layer in layers:
            features.extend(_build_features(layer, start_index=len(features)))
            if layer.crs_assumed:
                warnings.append(f"Layer '{layer.name}' had no CRS; assumed EPSG:4326 per the KML spec.")
            if layer.gdf.crs is None:
                warnings.append(f"Layer '{layer.name}' has no CRS; measurements were skipped.")

    if not features:
        warnings.append("File contains no features.")

    crs_values = {crs_label(layer.gdf.crs) for layer in layers}
    if len(crs_values) > 1:
        warnings.append("Layers use different CRSs.")
    source_crs = crs_values.pop() if len(crs_values) == 1 else None

    record = UploadedFile(
        original_filename=Path(filename or "upload").name[:255],
        file_type=file_type,
        size_bytes=len(content),
        source_crs=source_crs,
        crs_assumed=any(layer.crs_assumed for layer in layers),
        feature_count=len(features),
        warnings=warnings,
        features=features,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
