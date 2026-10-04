import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StockLine(BaseModel):
    sku_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    source_location_id: uuid.UUID | None = None
    source_container_id: uuid.UUID | None = None
    destination_location_id: uuid.UUID | None = None
    destination_container_id: uuid.UUID | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_direction(self):
        if self.source_location_id is None and self.destination_location_id is None:
            raise ValueError("Нужно указать источник и/или назначение")
        if self.source_container_id is not None and self.source_location_id is None:
            raise ValueError("Для source_container_id нужна source_location_id")
        if self.destination_container_id is not None and self.destination_location_id is None:
            raise ValueError("Для destination_container_id нужна destination_location_id")
        if (
            self.source_location_id == self.destination_location_id
            and self.source_container_id == self.destination_container_id
            and self.source_location_id is not None
        ):
            raise ValueError("Источник и назначение совпадают")
        return self


class OperationCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    operation_type: str
    idempotency_key: str = Field(min_length=1, max_length=128)
    performed_by: str | None = None
    reference: str | None = None
    comment: str | None = None
    lines: list[StockLine] = Field(min_length=1)


class ReceiveCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    performed_by: str | None = None
    reference: str | None = None
    comment: str | None = None
    sku_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    destination_location_id: uuid.UUID
    destination_container_id: uuid.UUID | None = None


class MoveCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    performed_by: str | None = None
    reference: str | None = None
    comment: str | None = None
    sku_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    source_location_id: uuid.UUID
    destination_location_id: uuid.UUID
    source_container_id: uuid.UUID | None = None
    destination_container_id: uuid.UUID | None = None


class ShipCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    performed_by: str | None = None
    reference: str | None = None
    comment: str | None = None
    sku_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    source_location_id: uuid.UUID
    source_container_id: uuid.UUID | None = None


class AdjustCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    performed_by: str | None = None
    reference: str | None = None
    comment: str | None = None
    sku_id: uuid.UUID
    delta: Decimal
    location_id: uuid.UUID
    container_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_delta(self):
        if self.delta == 0:
            raise ValueError("delta не может быть равен 0")
        return self


class CatalogAdjustCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    barcode: str = Field(min_length=1, max_length=255)
    location_id: uuid.UUID
    container_id: uuid.UUID | None = None
    delta: Decimal
    performed_by: str | None = None
    reason: str = Field(default="Ручная корректировка из карточки остатка", min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_delta(self):
        if self.delta == 0:
            raise ValueError("delta не может быть равен 0")
        return self


class MoveContainerCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    performed_by: str | None = None
    reference: str | None = None
    comment: str | None = None
    container_id: uuid.UUID
    destination_location_id: uuid.UUID


class OperationLineRead(ORMModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    quantity: Decimal
    source_location_id: uuid.UUID | None
    source_container_id: uuid.UUID | None
    destination_location_id: uuid.UUID | None
    destination_container_id: uuid.UUID | None
    reason: str | None


class OperationRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    operation_type: str
    status: str
    idempotency_key: str
    performed_by: str | None
    reference: str | None
    comment: str | None
    created_at: datetime
    lines: list[OperationLineRead] = []


class BalanceRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    location_id: uuid.UUID
    container_id: uuid.UUID | None
    quantity: Decimal
    version: int
