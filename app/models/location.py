import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class Location(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "locations"
    __table_args__ = (
        Index("uq_location_warehouse_code", "warehouse_id", "code", unique=True),
        Index("uq_location_warehouse_barcode", "warehouse_id", "barcode", unique=True),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    barcode: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_type: Mapped[str] = mapped_column(String(32), default="CELL", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
