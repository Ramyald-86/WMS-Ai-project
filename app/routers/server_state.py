"""API for durable client state previously kept only in browser storage."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import server_state_store as store

router = APIRouter(prefix="/server-state", tags=["Server state"])


class PendingWbReplace(BaseModel):
    client_id: str
    warehouse_id: str
    lines: list[dict[str, Any]] = Field(default_factory=list)


class PendingWbUpsert(BaseModel):
    client_id: str
    warehouse_id: str
    lines: list[dict[str, Any]] = Field(default_factory=list)


class PendingWbDelete(BaseModel):
    client_id: str
    warehouse_id: str
    idempotency_keys: list[str] | None = None
    clear_all: bool = False


class PendingOpUpsert(BaseModel):
    scope_key: str
    kind: str
    request_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    client_id: str | None = None
    warehouse_id: str | None = None


class PendingOpClear(BaseModel):
    scope_key: str
    request_id: str | None = None


class TerminalDraftPut(BaseModel):
    client_id: str
    warehouse_id: str
    payload: dict[str, Any]
    terminal_key: str = "default"


class MigrateLocalBody(BaseModel):
    client_id: str | None = None
    warehouse_id: str | None = None
    pending_wb: list[dict[str, Any]] | None = None
    pending_ops: list[dict[str, Any]] | None = None
    terminal_draft: dict[str, Any] | None = None
    terminal_key: str = "default"


@router.get("/pending-wb")
def get_pending_wb(
    client_id: str = Query(...),
    warehouse_id: str = Query(...),
    db: Session = Depends(get_db),
):
    return {"lines": store.list_pending_wb(db, client_id, warehouse_id)}


@router.put("/pending-wb")
def put_pending_wb(body: PendingWbReplace, db: Session = Depends(get_db)):
    lines = store.replace_pending_wb(db, body.client_id, body.warehouse_id, body.lines)
    return {"lines": lines}


@router.post("/pending-wb/upsert")
def upsert_pending_wb(body: PendingWbUpsert, db: Session = Depends(get_db)):
    lines = store.upsert_pending_wb_lines(db, body.client_id, body.warehouse_id, body.lines)
    return {"lines": lines}


@router.post("/pending-wb/delete")
def delete_pending_wb(body: PendingWbDelete, db: Session = Depends(get_db)):
    lines = store.delete_pending_wb(
        db,
        body.client_id,
        body.warehouse_id,
        idempotency_keys=body.idempotency_keys,
        clear_all=body.clear_all,
    )
    return {"lines": lines}


@router.get("/pending-ops")
def get_pending_ops(
    scope_key: str | None = None,
    db: Session = Depends(get_db),
):
    return {"ops": store.list_pending_ops(db, scope_key)}


@router.post("/pending-ops/upsert")
def upsert_pending_ops(body: PendingOpUpsert, db: Session = Depends(get_db)):
    op = store.upsert_pending_op(
        db,
        scope_key=body.scope_key,
        kind=body.kind,
        request_id=body.request_id,
        payload=body.payload,
        client_id=body.client_id,
        warehouse_id=body.warehouse_id,
    )
    return {"op": op}


@router.post("/pending-ops/clear")
def clear_pending_ops(body: PendingOpClear, db: Session = Depends(get_db)):
    n = store.clear_pending_op(db, body.scope_key, body.request_id)
    return {"cleared": n}


@router.get("/terminal-draft")
def get_terminal_draft(
    client_id: str = Query(...),
    warehouse_id: str = Query(...),
    terminal_key: str = "default",
    db: Session = Depends(get_db),
):
    row = store.get_terminal_draft(db, client_id, warehouse_id, terminal_key)
    return row or {"payload": None}


@router.put("/terminal-draft")
def put_terminal_draft(body: TerminalDraftPut, db: Session = Depends(get_db)):
    return store.put_terminal_draft(
        db, body.client_id, body.warehouse_id, body.payload, body.terminal_key
    )


@router.post("/migrate-local")
def migrate_local(body: MigrateLocalBody, db: Session = Depends(get_db)):
    """One-shot upload of browser localStorage/IDB leftovers into Postgres."""
    result: dict[str, Any] = {"pending_wb": 0, "pending_ops": 0, "terminal": False}
    if body.client_id and body.warehouse_id and body.pending_wb is not None:
        lines = store.upsert_pending_wb_lines(
            db, body.client_id, body.warehouse_id, body.pending_wb
        )
        result["pending_wb"] = len(lines)
    for op in body.pending_ops or []:
        if not op.get("scope_key") or not op.get("request_id") or not op.get("kind"):
            continue
        store.upsert_pending_op(
            db,
            scope_key=op["scope_key"],
            kind=op["kind"],
            request_id=op["request_id"],
            payload=op.get("payload") or op,
            client_id=op.get("client_id") or body.client_id,
            warehouse_id=op.get("warehouse_id") or body.warehouse_id,
        )
        result["pending_ops"] += 1
    if body.client_id and body.warehouse_id and body.terminal_draft is not None:
        store.put_terminal_draft(
            db,
            body.client_id,
            body.warehouse_id,
            body.terminal_draft,
            body.terminal_key,
        )
        result["terminal"] = True
    return result
