import uuid
from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ClientCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class ClientRead(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class WarehouseRead(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool


class LocationCreate(BaseModel):
    warehouse_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    code: str = Field(min_length=1, max_length=128)
    barcode: str = Field(min_length=1, max_length=255)
    name: str | None = None
    location_type: str = "CELL"


class LocationRead(ORMModel):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    parent_id: uuid.UUID | None
    code: str
    barcode: str
    name: str | None
    location_type: str
    is_active: bool


class SKUCreate(BaseModel):
    client_id: uuid.UUID
    sku_code: str = Field(min_length=1, max_length=128)
    article: str | None = None
    name: str = Field(min_length=1, max_length=255)
    size: str | None = None
    barcode: str | None = None
    unit: str = "PCS"


class SKURead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    sku_code: str
    article: str | None
    name: str
    size: str | None
    barcode: str | None
    unit: str
    is_active: bool


class ContainerCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    location_id: uuid.UUID | None = None
    barcode: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    container_type: str = "BOX"


class ContainerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    barcode: str | None = Field(default=None, min_length=1, max_length=255)


class ContainerRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    location_id: uuid.UUID | None
    barcode: str
    name: str | None
    container_type: str
    status: str
