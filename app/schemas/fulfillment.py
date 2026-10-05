"""Pydantic schemas for platform fulfillment onboarding API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


WarehouseKind = Literal["MAIN", "FBS", "PVZ", "TRANSIT", "DEFECT", "OTHER"]
UserRole = Literal["FC_ADMIN", "WAREHOUSE", "PICKER", "PACKER", "WB_OPERATOR", "VIEWER"]
FulfillmentStatus = Literal["DRAFT", "ONBOARDING", "ACTIVE", "SUSPENDED", "ARCHIVED"]
MarketplaceProvider = Literal["WB", "OZON"]


def _normalize_code(value: str) -> str:
    code = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip().upper())
    code = "-".join(part for part in code.replace("_", "-").split("-") if part)
    if not code:
        raise ValueError("Код не может быть пустым")
    return code[:64]


class FeatureProfilePatch(BaseModel):
    template: str | None = Field(default=None, max_length=64)
    template_version: str | None = Field(default=None, max_length=64)
    marketplaces: list[MarketplaceProvider] | None = None
    modules: dict[str, bool] | None = None
    policies: dict[str, bool] | None = None
    limits: dict[str, int] | None = None


class FulfillmentCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=255)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=64)
    locale: str = Field(default="ru", min_length=2, max_length=16)
    subdomain: str | None = Field(default=None, max_length=128)
    custom_domain: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=4000)
    feature_profile: FeatureProfilePatch | None = None
    # Optional bootstrap pieces so the wizard can create a usable draft in one call.
    admin_username: str | None = Field(default=None, min_length=1, max_length=100)
    admin_display_name: str | None = Field(default=None, min_length=1, max_length=255)
    admin_email: str | None = Field(default=None, max_length=255)
    admin_password: str | None = Field(default=None, min_length=8, max_length=500)
    primary_warehouse_code: str | None = Field(default=None, min_length=1, max_length=64)
    primary_warehouse_name: str | None = Field(default=None, min_length=1, max_length=255)
    primary_warehouse_kind: WarehouseKind = "MAIN"

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _normalize_code(value)

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip().lower()[:128]

    @field_validator("custom_domain")
    @classmethod
    def validate_domain(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip().lower()[:255]

    @model_validator(mode="after")
    def admin_password_requires_username(self):
        if self.admin_password and not self.admin_username:
            raise ValueError("Для admin_password нужен admin_username")
        if self.admin_username and not self.admin_password:
            raise ValueError("Для admin_username нужен admin_password")
        return self


class FulfillmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=255)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    locale: str | None = Field(default=None, min_length=2, max_length=16)
    subdomain: str | None = Field(default=None, max_length=128)
    custom_domain: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=4000)
    feature_profile: FeatureProfilePatch | None = None

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            return None
        return value.strip().lower()[:128]

    @field_validator("custom_domain")
    @classmethod
    def validate_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            return None
        return value.strip().lower()[:255]


class FulfillmentRead(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    timezone: str
    locale: str
    subdomain: str | None
    custom_domain: str | None
    status: FulfillmentStatus
    is_default: bool
    feature_profile: dict[str, Any]
    notes: str | None
    activated_at: datetime | None
    suspended_at: datetime | None
    archived_at: datetime | None
    created_by: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime
    has_print_agent_token: bool = False


class FulfillmentSummary(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    status: FulfillmentStatus
    is_default: bool
    subdomain: str | None
    timezone: str
    warehouses_count: int = 0
    clients_count: int = 0
    users_count: int = 0
    print_stations_count: int = 0
    print_stations_active_count: int = 0
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None


class WarehouseLinkCreate(BaseModel):
    """Create a new warehouse and attach it to the fulfillment."""

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    warehouse_kind: WarehouseKind = "MAIN"
    is_primary: bool = False
    address: str | None = Field(default=None, max_length=500)
    contact_phone: str | None = Field(default=None, max_length=64)
    contact_email: str | None = Field(default=None, max_length=255)
    # Optional: attach an already existing warehouse instead of creating one.
    existing_warehouse_id: uuid.UUID | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _normalize_code(value)


class WarehouseLinkUpdate(BaseModel):
    warehouse_kind: WarehouseKind | None = None
    is_primary: bool | None = None
    address: str | None = Field(default=None, max_length=500)
    contact_phone: str | None = Field(default=None, max_length=64)
    contact_email: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class WarehouseLinkRead(ORMModel):
    id: uuid.UUID
    fulfillment_id: uuid.UUID
    warehouse_id: uuid.UUID
    warehouse_code: str
    warehouse_name: str
    warehouse_is_active: bool
    warehouse_kind: WarehouseKind
    is_primary: bool
    address: str | None
    contact_phone: str | None
    contact_email: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ClientLinkCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    is_pilot: bool = False
    existing_client_id: uuid.UUID | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _normalize_code(value)


class ClientLinkUpdate(BaseModel):
    is_pilot: bool | None = None
    is_active: bool | None = None


class ClientLinkRead(ORMModel):
    id: uuid.UUID
    fulfillment_id: uuid.UUID
    client_id: uuid.UUID
    client_code: str
    client_name: str
    client_is_active: bool
    is_pilot: bool
    is_active: bool
    marketplace_credentials_count: int = 0
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=500)
    email: str | None = Field(default=None, max_length=255)
    role: UserRole = "FC_ADMIN"
    must_change_password: bool = True

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = value.strip().lower()
        if not username:
            raise ValueError("username пуст")
        return username[:100]


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=500)


class UserRead(ORMModel):
    id: uuid.UUID
    fulfillment_id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    role: UserRole
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class PrintStationCreate(BaseModel):
    station_code: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=255)
    printer_name: str | None = Field(default=None, max_length=255)
    label_format: str = Field(default="58x40", max_length=32)
    notes: str | None = Field(default=None, max_length=2000)


class PrintStationUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    printer_name: str | None = Field(default=None, max_length=255)
    label_format: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class PrintStationRead(ORMModel):
    id: uuid.UUID
    fulfillment_id: uuid.UUID
    station_code: str
    name: str | None
    printer_name: str | None
    label_format: str
    is_active: bool
    last_seen_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class MarketplaceCredentialAttach(BaseModel):
    provider: MarketplaceProvider = "WB"
    name: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=5)
    external_client_id: str | None = None

    @model_validator(mode="after")
    def ozon_requires_client_id(self):
        if self.provider == "OZON" and not (self.external_client_id or "").strip():
            raise ValueError("Для Ozon нужен Client-Id")
        return self


class MarketplaceCredentialAttachRead(BaseModel):
    credential_id: uuid.UUID
    client_id: uuid.UUID
    fulfillment_id: uuid.UUID
    provider: str
    name: str
    external_client_id: str | None
    is_active: bool


class ReadinessCheck(BaseModel):
    key: str
    ok: bool
    required: bool
    message: str


class ReadinessReport(BaseModel):
    fulfillment_id: uuid.UUID
    status: FulfillmentStatus
    ready_to_activate: bool
    checks: list[ReadinessCheck]


class PrintTokenReveal(BaseModel):
    fulfillment_id: uuid.UUID
    token: str
    station_code: str | None = None
    station_bound: bool = False


class FulfillmentEventRead(ORMModel):
    id: uuid.UUID
    fulfillment_id: uuid.UUID
    event_type: str
    actor: str | None
    message: str | None
    data: dict[str, Any]
    created_at: datetime


class ActivateResponse(BaseModel):
    ok: bool
    fulfillment: FulfillmentRead
    readiness: ReadinessReport
