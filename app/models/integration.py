import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class MarketplaceCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "marketplace_credentials"
    __table_args__ = (
        Index(
            "uq_marketplace_credential",
            "client_id",
            "provider",
            "name",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index("ix_marketplace_credential_client_provider", "client_id", "provider"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    external_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Per seller-warehouse mode: WORK (full warehouse actions), WATCH (read-only), HIDDEN.
    warehouse_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class MarketplaceOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Local projection of marketplace orders.

    Wildberries is still the source of truth for supplierStatus/wbStatus. FulfillCore owns
    the warehouse packing workflow state (product scan, CHZ scan, label generation).
    """

    __tablename__ = "marketplace_orders"
    __table_args__ = (
        Index(
            "uq_marketplace_order_external",
            "credential_id",
            "provider",
            "external_order_id",
            unique=True,
        ),
        Index("ix_marketplace_order_client_provider", "client_id", "provider"),
        Index("ix_marketplace_order_credential_status", "credential_id", "local_status"),
        Index("uq_marketplace_order_chz", "credential_id", "chz_code", unique=True),
    )

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("marketplace_credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="WB")
    external_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_order_num: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    article: Mapped[str | None] = mapped_column(String(255), nullable=True)
    barcodes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    required_meta: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    optional_meta: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    wb_warehouse_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cargo_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_sku_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skus.id", ondelete="SET NULL"), nullable=True, index=True
    )

    supplier_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wb_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_cancellable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    local_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NEW")
    # True when a warehouse user intentionally forced NEW/PICKING even though WB already has
    # both marking code and order sticker. Automatic WB synchronization must respect it.
    manual_status_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supply_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    product_scanned_barcode: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chz_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    chz_attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    label_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    label_print_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assembled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # MVP 0.5: local workflow is driven by a printed picking list, then packing.
    picking_list_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_picking_list_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    packed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    external_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class MarketplacePickingList(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "marketplace_picking_lists"
    __table_args__ = (
        Index("ix_marketplace_picking_lists_credential_created", "credential_id", "created_at"),
    )

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("marketplace_credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    order_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    supply_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class MarketplaceOrderEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "marketplace_order_events"
    __table_args__ = (
        Index("ix_marketplace_order_event_order_time", "marketplace_order_id", "created_at"),
    )

    marketplace_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("marketplace_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketplaceProduct(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Local product-card projection imported from a marketplace catalog."""

    __tablename__ = "marketplace_products"
    __table_args__ = (
        Index("uq_marketplace_product_external", "credential_id", "provider", "external_product_id", unique=True),
        Index("ix_marketplace_product_client_provider", "client_id", "provider"),
        Index("ix_marketplace_product_vendor_code", "credential_id", "vendor_code"),
    )

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("marketplace_credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="WB")
    external_product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vendor_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photos: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    sizes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    barcodes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    local_sku_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skus.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ImportBatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "import_batches"
    __table_args__ = (
        Index("ix_import_batch_client_warehouse", "client_id", "warehouse_id"),
        Index("ix_import_batch_checksum", "client_id", "warehouse_id", "checksum"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETED")
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_skus: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_containers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_locations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_operations.id", ondelete="SET NULL"), nullable=True
    )
    performed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
