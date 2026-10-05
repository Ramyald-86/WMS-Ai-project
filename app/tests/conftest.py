"""Shared fixtures for fulfillment platform tests (SQLite in-memory)."""

from __future__ import annotations

import os
import uuid

# Must be set before app.core.config / database are imported by the service stack.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("APP_SECRET_KEY", "fulfillcore-test-secret-key-for-platform")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault(
    "ADMIN_PASSWORD_HASH",
    "pbkdf2_sha256$310000$b-2n3GGnKSVLfktryo4pxQ$DQeaVVq_3xXJvwPzRFuJlVMifG-1rVU5R9xWbw6oPfw",
)
os.environ.setdefault("PRINT_AGENT_TOKEN", "test-print-agent-token")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models import Client, MarketplaceCredential, Warehouse
from app.models.fulfillment import (
    Fulfillment,
    FulfillmentClient,
    FulfillmentEvent,
    FulfillmentPrintStation,
    FulfillmentUser,
    FulfillmentWarehouse,
)
from app.routers.platform_fulfillments import router as platform_router


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(_type, _compiler, **_kw):
    return "CHAR(36)"


FULFILLMENT_TABLES = [
    Client.__table__,
    Warehouse.__table__,
    MarketplaceCredential.__table__,
    Fulfillment.__table__,
    FulfillmentWarehouse.__table__,
    FulfillmentClient.__table__,
    FulfillmentUser.__table__,
    FulfillmentPrintStation.__table__,
    FulfillmentEvent.__table__,
]


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine, tables=FULFILLMENT_TABLES)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine, tables=FULFILLMENT_TABLES)
        engine.dispose()


@pytest.fixture()
def db(db_engine) -> Session:
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seed_legacy(db: Session):
    """Existing single-instance masterdata that must land in DEFAULT fulfillment."""
    wh = Warehouse(id=uuid.uuid4(), code="LEGACY-WH", name="Legacy warehouse", is_active=True)
    client = Client(id=uuid.uuid4(), code="LEGACY-CL", name="Legacy client", is_active=True)
    db.add(wh)
    db.add(client)
    db.commit()
    return {"warehouse": wh, "client": client}


@pytest.fixture()
def api_client(db_engine) -> TestClient:
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

    app = FastAPI()
    app.include_router(platform_router)

    def _override_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as client:
        yield client
