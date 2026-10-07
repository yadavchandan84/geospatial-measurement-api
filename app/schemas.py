"""Pydantic response models."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    feature_index: int
    layer: str | None
    name: str | None
    geometry_type: str | None
    status: Literal["measured", "unsupported"]
    reason: str | None = None
    properties: dict[str, Any]
    geometry: dict[str, Any] | None = Field(
        default=None,
        description="GeoJSON geometry (EPSG:4326 when the source CRS is known).",
    )


class FileSummary(BaseModel):
    measured_features: int
    unsupported_features: int


class FileOut(BaseModel):
    id: str
    original_filename: str
    file_type: Literal["kml", "shapefile"]
    size_bytes: int
    source_crs: str | None
    crs_assumed: bool
    feature_count: int
    warnings: list[str]
    created_at: datetime
    summary: FileSummary
    features: list[FeatureOut]


class FeatureMeasurement(BaseModel):
    feature_id: int
    feature_index: int
    layer: str | None
    name: str | None
    geometry_type: str | None
    status: Literal["measured", "unsupported"]
    reason: str | None = None
    area_m2: float | None = None
    area_km2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    is_valid: bool | None = None
    repaired: bool = False


class MeasurementTotals(BaseModel):
    area_m2: float
    area_km2: float
    perimeter_m: float
    length_m: float
    measured_features: int
    unsupported_features: int


class MeasurementsOut(BaseModel):
    file_id: str
    source_crs: str | None
    measurement_crs: str = "EPSG:4326"
    method: str = "geodesic on the WGS84 ellipsoid (pyproj.Geod)"
    units: dict[str, str] = {"area": "m^2", "length": "m", "perimeter": "m"}
    totals: MeasurementTotals
    features: list[FeatureMeasurement]


class ErrorOut(BaseModel):
    detail: str
