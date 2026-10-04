import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class InventoryBalance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        Index("uq_inventory_bucket_key", "bucket_key", unique=True),
        Index("ix_inventory_client_sku", "client_id", "sku_id"),
        Index("ix_inventory_location", "location_id"),
        Index("ix_inventory_container", "container_id"),
    )

    bucket_key: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    sku_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("skus.id"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False)
    container_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("containers.id"), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal("0"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class InventoryOperation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "inventory_operations"
    __table_args__ = (
        Index("uq_operation_client_idempotency", "client_id", "idempotency_key", unique=True),
        Index("ix_operation_created_at", "created_at"),
        Index("ix_operation_type", "operation_type"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    performed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class InventoryOperationLine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "inventory_operation_lines"
    __table_args__ = (
        Index("ix_operation_line_operation", "operation_id"),
        Index("ix_operation_line_sku", "sku_id"),
    )

    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_operations.id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("skus.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    source_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=True)
    source_container_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("containers.id"), nullable=True)
    destination_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=True)
    destination_container_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("containers.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
