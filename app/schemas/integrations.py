import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MarketplaceCredentialCreate(BaseModel):
    client_id: uuid.UUID
    provider: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=5)
    external_client_id: str | None = None

    @model_validator(mode="after")
    def normalize_provider(self):
        self.provider = self.provider.upper()
        if self.provider not in {"WB", "OZON"}:
            raise ValueError("provider должен быть WB или OZON")
        if self.provider == "OZON" and not self.external_client_id:
            raise ValueError("Для Ozon нужен Client-Id")
        return self


class MarketplaceCredentialRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    provider: str
    name: str
    external_client_id: str | None
    is_active: bool
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None
    warehouse_settings: dict[str, Any] = Field(default_factory=dict)




class WBWarehouseModeItem(BaseModel):
    warehouse_id: int
    mode: str = Field(default="HIDDEN", pattern="^(WORK|WATCH|HIDDEN)$")


class WBWarehouseSettingsUpdate(BaseModel):
    credential_id: uuid.UUID
    items: list[WBWarehouseModeItem]

class WBCreateSupply(BaseModel):
    credential_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128)
    order_ids: list[int] = Field(default_factory=list, max_length=100)


class WBAddOrders(BaseModel):
    credential_id: uuid.UUID
    order_ids: list[int] = Field(min_length=1, max_length=100)


class WBOrderLabelsRequest(BaseModel):
    credential_id: uuid.UUID
    order_ids: list[int] = Field(min_length=1, max_length=100)


class WBBoxesRequest(BaseModel):
    credential_id: uuid.UUID
    amount: int = Field(ge=1, le=1000)


class WBBoxLabelsRequest(BaseModel):
    credential_id: uuid.UUID
    trbx_ids: list[str] = Field(min_length=1, max_length=1000)


class OzonLabelsRequest(BaseModel):
    credential_id: uuid.UUID
    posting_numbers: list[str] = Field(min_length=1, max_length=20)


class OzonShipRequest(BaseModel):
    credential_id: uuid.UUID
    posting_number: str
    packages: list[dict[str, Any]] = Field(min_length=1)


class WBSyncRequest(BaseModel):
    credential_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    # Exact seller warehouse currently opened in the WB workspace. FulfillCore never
    # mixes WORK/WATCH warehouses on one order board.
    wb_warehouse_id: int | None = None
    days: int = Field(default=30, ge=1, le=30)
    force: bool = False


class WBMetaRefreshRequest(BaseModel):
    credential_id: uuid.UUID
    order_ids: list[int] = Field(min_length=1, max_length=500)


class WBScanProductRequest(BaseModel):
    credential_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    barcode: str = Field(min_length=1, max_length=255)


class WBScanCHZRequest(BaseModel):
    credential_id: uuid.UUID
    order_id: uuid.UUID
    chz: str = Field(min_length=1, max_length=1000)
    print_station: str | None = Field(default=None, max_length=64)


class WBDeleteCHZRequest(BaseModel):
    credential_id: uuid.UUID


class WBMarkInSupplyRequest(BaseModel):
    credential_id: uuid.UUID
    supply_id: str = Field(min_length=1, max_length=64)
    order_ids: list[int] = Field(min_length=1, max_length=100)

class WBPickingListRequest(BaseModel):
    credential_id: uuid.UUID
    warehouse_id: uuid.UUID
    order_ids: list[int] = Field(min_length=1, max_length=5000)
    name: str | None = Field(default=None, max_length=255)
    created_by: str | None = Field(default=None, max_length=255)


class WBQueuePrintRequest(BaseModel):
    credential_id: uuid.UUID
    print_station: str = Field(min_length=1, max_length=64)
    barcode: str | None = Field(default=None, max_length=255)


class WBProductImportRequest(BaseModel):
    credential_id: uuid.UUID
    articles: list[str] = Field(default_factory=list, max_length=5000)
    import_all: bool = False


class WBWorkflowResetRequest(BaseModel):
    credential_id: uuid.UUID
    order_ids: list[int] = Field(min_length=1, max_length=5000)
    target_status: str = Field(pattern="^(NEW|PICKING)$")


class WBShipPackedRequest(BaseModel):
    credential_id: uuid.UUID
    order_ids: list[int] = Field(min_length=1, max_length=5000)
