"""HTTP routes for uploading files and reading features / measurements."""

import json
from datetime import timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Feature, UploadedFile
from app.schemas import (
    ErrorOut,
    FeatureMeasurement,
    FeatureOut,
    FileOut,
    FileSummary,
    MeasurementsOut,
    MeasurementTotals,
)
from app.services.ingest import ingest_upload
from app.services.measurement import MEASURED
from app.services.validation import UploadError

router = APIRouter(prefix="/api/files", tags=["files"])

ERROR_RESPONSES = {
    400: {"model": ErrorOut, "description": "Invalid upload (bad zip, missing shapefile parts, ...)"},
    413: {"model": ErrorOut, "description": "File too large"},
    415: {"model": ErrorOut, "description": "Unsupported file extension"},
    422: {"model": ErrorOut, "description": "File could not be parsed as geospatial data"},
}


def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """Read the upload in chunks and stop as soon as the size limit is exceeded."""
    chunks, total = [], 0
    while chunk := upload.file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise UploadError(f"File exceeds the {limit // (1024 * 1024)} MB upload limit.", 413)
        chunks.append(chunk)
    return b"".join(chunks)


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"File '{file_id}' not found.")
    return record


def _feature_out(f: Feature, include_geometry: bool) -> FeatureOut:
    return FeatureOut(
        id=f.id,
        feature_index=f.feature_index,
        layer=f.layer,
        name=f.name,
        geometry_type=f.geometry_type,
        status=f.status,
        reason=f.reason,
        properties=f.properties or {},
        geometry=json.loads(f.geometry_geojson) if include_geometry and f.geometry_geojson else None,
    )


def _file_out(record: UploadedFile, include_geometry: bool = True) -> FileOut:
    measured = sum(1 for f in record.features if f.status == MEASURED)
    return FileOut(
        id=record.id,
        original_filename=record.original_filename,
        file_type=record.file_type,
        size_bytes=record.size_bytes,
        source_crs=record.source_crs,
        crs_assumed=record.crs_assumed,
        feature_count=record.feature_count,
        warnings=record.warnings or [],
        # SQLite drops tzinfo on read; timestamps are always stored in UTC.
        created_at=record.created_at
        if record.created_at.tzinfo
        else record.created_at.replace(tzinfo=timezone.utc),
        summary=FileSummary(
            measured_features=measured,
            unsupported_features=record.feature_count - measured,
        ),
        features=[_feature_out(f, include_geometry) for f in record.features],
    )


@router.post(
    "/",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    summary="Upload a .kml file or a .zip containing a shapefile",
)
def upload_file(
    file: UploadFile = File(..., description=".kml, or .zip with .shp/.shx/.dbf (+ .prj)"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileOut:
    try:
        content = _read_limited(file, settings.max_upload_bytes)
        record = ingest_upload(db, file.filename, content, settings)
    except UploadError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _file_out(record)


@router.get(
    "/{file_id}/",
    response_model=FileOut,
    responses={404: {"model": ErrorOut}},
    summary="Get file metadata and its features",
)
def get_file(
    file_id: str,
    include_geometry: bool = Query(True, description="Include GeoJSON geometries in the response"),
    db: Session = Depends(get_db),
) -> FileOut:
    return _file_out(_get_file_or_404(db, file_id), include_geometry)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsOut,
    responses={404: {"model": ErrorOut}},
    summary="Get geodesic area / perimeter / length per feature and totals",
)
def get_measurements(file_id: str, db: Session = Depends(get_db)) -> MeasurementsOut:
    record = _get_file_or_404(db, file_id)

    items: list[FeatureMeasurement] = []
    total_area = total_perimeter = total_length = 0.0
    measured = 0
    for f in record.features:
        if f.status == MEASURED:
            measured += 1
            total_area += f.area_m2 or 0.0
            total_perimeter += f.perimeter_m or 0.0
            total_length += f.length_m or 0.0
        items.append(
            FeatureMeasurement(
                feature_id=f.id,
                feature_index=f.feature_index,
                layer=f.layer,
                name=f.name,
                geometry_type=f.geometry_type,
                status=f.status,
                reason=f.reason,
                area_m2=f.area_m2,
                area_km2=f.area_m2 / 1_000_000 if f.area_m2 is not None else None,
                perimeter_m=f.perimeter_m,
                length_m=f.length_m,
                is_valid=f.is_valid,
                repaired=f.repaired,
            )
        )

    return MeasurementsOut(
        file_id=record.id,
        source_crs=record.source_crs,
        totals=MeasurementTotals(
            area_m2=total_area,
            area_km2=total_area / 1_000_000,
            perimeter_m=total_perimeter,
            length_m=total_length,
            measured_features=measured,
            unsupported_features=len(items) - measured,
        ),
        features=items,
    )
