"""Platform multi-fulfillment (tenant) models.

These tables are additive. Existing Client / Warehouse / PrintJob / Marketplace*
rows keep working unchanged. Links map current masterdata into a fulfillment
without rewriting warehouse operational paths.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


FULFILLMENT_STATUSES = ("DRAFT", "ONBOARDING", "ACTIVE", "SUSPENDED", "ARCHIVED")
WAREHOUSE_KINDS = ("MAIN", "FBS", "PVZ", "TRANSIT", "DEFECT", "OTHER")
USER_ROLES = ("FC_ADMIN", "WAREHOUSE", "PICKER", "PACKER", "WB_OPERATOR", "VIEWER")


class Fulfillment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fulfillments"
    __table_args__ = (
        Index("ix_fulfillments_status", "status"),
        Index("ix_fulfillments_is_default", "is_default"),
    )

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="ru")
    subdomain: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    custom_domain: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    # Platform seed for the current single-instance deployment.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    feature_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Encrypted master token for future per-FC Print Agents. Existing global
    # PRINT_AGENT_TOKEN middleware is intentionally left untouched.
    encrypted_print_agent_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


class FulfillmentWarehouse(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fulfillment_warehouses"
    __table_args__ = (
        UniqueConstraint("fulfillment_id", "warehouse_id", name="uq_fulfillment_warehouse"),
        # One physical warehouse belongs to exactly one fulfillment.
        UniqueConstraint("warehouse_id", name="uq_fulfillment_warehouse_exclusive"),
        Index("ix_fulfillment_warehouses_fulfillment", "fulfillment_id"),
        Index("ix_fulfillment_warehouses_warehouse", "warehouse_id"),
    )

    fulfillment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fulfillments.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="MAIN")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FulfillmentClient(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fulfillment_clients"
    __table_args__ = (
        UniqueConstraint("fulfillment_id", "client_id", name="uq_fulfillment_client"),
        # One seller client belongs to exactly one fulfillment.
        UniqueConstraint("client_id", name="uq_fulfillment_client_exclusive"),
        Index("ix_fulfillment_clients_fulfillment", "fulfillment_id"),
        Index("ix_fulfillment_clients_client", "client_id"),
    )

    fulfillment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fulfillments.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    is_pilot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FulfillmentUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fulfillment_users"
    __table_args__ = (
        UniqueConstraint("fulfillment_id", "username", name="uq_fulfillment_user_username"),
        Index("ix_fulfillment_users_fulfillment", "fulfillment_id"),
        Index(
            "ix_fulfillment_users_active",
            "fulfillment_id",
            "is_active",
            postgresql_where=text("is_active = true"),
        ),
    )

    fulfillment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fulfillments.id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="FC_ADMIN")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


class FulfillmentPrintStation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fulfillment_print_stations"
    __table_args__ = (
        UniqueConstraint("fulfillment_id", "station_code", name="uq_fulfillment_print_station"),
        Index("ix_fulfillment_print_stations_fulfillment", "fulfillment_id"),
    )

    fulfillment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fulfillments.id", ondelete="CASCADE"), nullable=False
    )
    station_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    printer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    label_format: Mapped[str] = mapped_column(String(32), nullable=False, default="58x40")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class FulfillmentEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only audit trail for platform onboarding actions."""

    __tablename__ = "fulfillment_events"
    __table_args__ = (
        Index("ix_fulfillment_events_fulfillment_created", "fulfillment_id", "created_at"),
    )

    fulfillment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fulfillments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
