from __future__ import annotations

import os
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import httpx
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

import app.services.marketplaces as mp
from app.models import InventoryBalance
from app.services.printing import _print_payload_type


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_wb_get_retries_transport_error_but_write_does_not() -> None:
    original_wait = mp._wb_wait_for_slot
    original_sleep = mp.time.sleep
    mp._wb_wait_for_slot = lambda _key: None
    mp.time.sleep = lambda _seconds: None
    try:
        client = mp.WBClient("test-token")
        calls = {"n": 0}

        def flaky_get(method, path, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ReadTimeout("temporary timeout")
            return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, "https://example.invalid" + path))

        client._client.request = flaky_get
        response = client._request("GET", "/ping")
        _assert(response.status_code == 200, "GET retry should recover")
        _assert(calls["n"] == 3, f"GET expected 3 attempts, got {calls['n']}")
        client.close()

        client = mp.WBClient("test-token-write")
        write_calls = {"n": 0}

        def broken_write(method, path, **kwargs):
            write_calls["n"] += 1
            raise httpx.ReadTimeout("lost write response")

        client._client.request = broken_write
        try:
            client._request("PATCH", "/api/v3/orders/1/meta", json={"x": 1})
            raise AssertionError("write timeout must raise")
        except HTTPException as exc:
            _assert(exc.status_code == 504, "write timeout should become 504")
            _assert("перечитать" in str(exc.detail).lower(), "write timeout must instruct state re-read")
        _assert(write_calls["n"] == 1, f"write must not be blindly replayed, got {write_calls['n']} attempts")
        client.close()
    finally:
        mp._wb_wait_for_slot = original_wait
        mp.time.sleep = original_sleep


def test_ozon_read_only_post_retries_but_mutation_does_not() -> None:
    original_sleep = mp.time.sleep
    mp.time.sleep = lambda _seconds: None
    try:
        client = mp.OzonClient("client", "key")
        read_calls = {"n": 0}

        def flaky_post(path, **kwargs):
            read_calls["n"] += 1
            if read_calls["n"] < 3:
                raise httpx.ReadTimeout("temporary timeout")
            return httpx.Response(200, json={"result": []}, request=httpx.Request("POST", "https://example.invalid" + path))

        client._client.post = flaky_post
        client.warehouses()
        _assert(read_calls["n"] == 3, f"Ozon read-only POST expected 3 attempts, got {read_calls['n']}")
        client.close()

        client = mp.OzonClient("client", "key")
        write_calls = {"n": 0}

        def broken_post(path, **kwargs):
            write_calls["n"] += 1
            raise httpx.ReadTimeout("lost mutation response")

        client._client.post = broken_post
        try:
            client.ship("123", [{"products": []}])
            raise AssertionError("Ozon mutation timeout must raise")
        except HTTPException as exc:
            _assert(exc.status_code == 504, "Ozon mutation timeout should become 504")
        _assert(write_calls["n"] == 1, f"Ozon mutation must not replay, got {write_calls['n']} attempts")
        client.close()
    finally:
        mp.time.sleep = original_sleep


def test_print_payload_detection_is_signature_first() -> None:
    _assert(_print_payload_type(b"\x89PNG\r\n\x1a\nxxx", "wrong.pdf", "application/pdf") == "image/png", "PNG signature must win")
    _assert(_print_payload_type(b"%PDF-1.7\n", "wrong.png", "image/png") == "application/pdf", "PDF signature must win")
    _assert(_print_payload_type(b"unknown", "label.png", "application/pdf") == "image/png", "extension should recover stale MIME")


def test_postgres_balance_upsert_compiles() -> None:
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    row = {
        "id": uuid.uuid4(),
        "bucket_key": "bucket",
        "client_id": uuid.uuid4(),
        "warehouse_id": uuid.uuid4(),
        "sku_id": uuid.uuid4(),
        "location_id": uuid.uuid4(),
        "container_id": None,
        "quantity": 3,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    stmt = pg_insert(InventoryBalance).values([row])
    stmt = stmt.on_conflict_do_update(
        index_elements=[InventoryBalance.bucket_key],
        set_={
            "quantity": InventoryBalance.quantity + stmt.excluded.quantity,
            "version": InventoryBalance.version + 1,
            "updated_at": now,
        },
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    _assert("ON CONFLICT" in sql and "DO UPDATE" in sql, "PostgreSQL UPSERT did not compile")


def main() -> None:
    test_wb_get_retries_transport_error_but_write_does_not()
    test_ozon_read_only_post_retries_but_mutation_does_not()
    test_print_payload_detection_is_signature_first()
    test_postgres_balance_upsert_compiles()
    print("OK: data reliability patch regression checks passed")


if __name__ == "__main__":
    main()
