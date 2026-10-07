"""ORM models: one UploadedFile has many Features."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(20))  # "kml" | "shapefile"
    size_bytes: Mapped[int] = mapped_column(Integer)

    # CRS as declared by the source file (None when missing).
    source_crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # True when the CRS was not in the file but implied by the format (KML => WGS84).
    crs_assumed: Mapped[bool] = mapped_column(Boolean, default=False)

    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        order_by="Feature.feature_index",
    )


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id", ondelete="CASCADE"), index=True)
    feature_index: Mapped[int] = mapped_column(Integer)
    layer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Geometry type as found in the source (e.g. "MultiPolygon"), None for null geometry.
    geometry_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # GeoJSON geometry. In EPSG:4326 when the CRS is known, otherwise in source coordinates.
    geometry_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)

    # Measurement results (computed at ingestion time).
    status: Mapped[str] = mapped_column(String(20))  # "measured" | "unsupported"
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    perimeter_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    repaired: Mapped[bool] = mapped_column(Boolean, default=False)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
