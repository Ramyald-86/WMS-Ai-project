import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class SKU(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "skus"
    __table_args__ = (
        Index("uq_sku_client_code", "client_id", "sku_code", unique=True),
        Index("ix_sku_client_barcode", "client_id", "barcode"),
        Index("ix_sku_client_article_size", "client_id", "article", "size"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sku_code: Mapped[str] = mapped_column(String(128), nullable=False)
    article: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[str | None] = mapped_column(String(128), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str] = mapped_column(String(32), default="PCS", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
