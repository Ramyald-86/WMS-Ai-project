"""Durable browser→server state: pending WB queue, client pending ops, terminal drafts.

Receiving drafts/scans already live in fc_r145f27_* via r145f30/32/51 — this module
covers the remaining localStorage SoT that must survive cleared browser data.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

DDL = [
    """
    CREATE TABLE IF NOT EXISTS fc_pending_wb_receiving (
      id uuid PRIMARY KEY,
      client_id uuid NOT NULL,
      warehouse_id uuid NOT NULL,
      barcode varchar(255) NOT NULL,
      quantity integer NOT NULL,
      mode varchar(16) NOT NULL DEFAULT 'ADD',
      idempotency_key varchar(128),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_fc_pending_wb_idem
      ON fc_pending_wb_receiving (client_id, warehouse_id, idempotency_key)
      WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fc_pending_wb_scope
      ON fc_pending_wb_receiving (client_id, warehouse_id, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS fc_client_pending_ops (
      id uuid PRIMARY KEY,
      client_id uuid,
      warehouse_id uuid,
      scope_key text NOT NULL,
      kind varchar(32) NOT NULL,
      request_id varchar(96) NOT NULL,
      payload jsonb NOT NULL,
      status varchar(16) NOT NULL DEFAULT 'PENDING',
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (scope_key, request_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fc_client_pending_ops_scope
      ON fc_client_pending_ops (scope_key, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS fc_terminal_drafts (
      id uuid PRIMARY KEY,
      client_id uuid NOT NULL,
      warehouse_id uuid NOT NULL,
      terminal_key text NOT NULL,
      payload jsonb NOT NULL,
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (client_id, warehouse_id, terminal_key)
    )
    """,
]


def install_schema(db: Session) -> None:
    for stmt in DDL:
        db.execute(text(stmt))
    db.commit()


def _uuid(value: Any, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Некорректный {label}") from exc


def _row_wb(r) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "barcode": r["barcode"],
        "quantity": int(r["quantity"] or 0),
        "mode": r["mode"] or "ADD",
        "idempotency_key": r["idempotency_key"] or None,
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


def list_pending_wb(db: Session, client_id: str, warehouse_id: str) -> list[dict[str, Any]]:
    c, w = _uuid(client_id, "client_id"), _uuid(warehouse_id, "warehouse_id")
    rows = db.execute(
        text(
            """
            SELECT id, barcode, quantity, mode, idempotency_key, created_at
            FROM fc_pending_wb_receiving
            WHERE client_id=:c AND warehouse_id=:w
            ORDER BY created_at ASC, id ASC
            """
        ),
        {"c": str(c), "w": str(w)},
    ).mappings().all()
    return [_row_wb(r) for r in rows]


def replace_pending_wb(
    db: Session, client_id: str, warehouse_id: str, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    c, w = _uuid(client_id, "client_id"), _uuid(warehouse_id, "warehouse_id")
    db.execute(
        text("DELETE FROM fc_pending_wb_receiving WHERE client_id=:c AND warehouse_id=:w"),
        {"c": str(c), "w": str(w)},
    )
    for raw in lines or []:
        if not raw or not raw.get("barcode"):
            continue
        key = str(raw.get("idempotency_key") or "").strip() or None
        db.execute(
            text(
                """
                INSERT INTO fc_pending_wb_receiving
                  (id, client_id, warehouse_id, barcode, quantity, mode, idempotency_key)
                VALUES (:id, :c, :w, :b, :q, :m, :k)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "c": str(c),
                "w": str(w),
                "b": str(raw["barcode"])[:255],
                "q": int(raw.get("quantity") or 0),
                "m": str(raw.get("mode") or "ADD")[:16],
                "k": key[:128] if key else None,
            },
        )
    db.commit()
    return list_pending_wb(db, str(c), str(w))


def upsert_pending_wb_lines(
    db: Session, client_id: str, warehouse_id: str, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    c, w = _uuid(client_id, "client_id"), _uuid(warehouse_id, "warehouse_id")
    for raw in lines or []:
        if not raw or not raw.get("barcode"):
            continue
        key = str(raw.get("idempotency_key") or "").strip() or None
        qty = int(raw.get("quantity") or 0)
        mode = str(raw.get("mode") or "ADD")[:16]
        barcode = str(raw["barcode"])[:255]
        if key:
            updated = db.execute(
                text(
                    """
                    UPDATE fc_pending_wb_receiving
                    SET barcode=:b, quantity=:q, mode=:m, updated_at=now()
                    WHERE client_id=:c AND warehouse_id=:w AND idempotency_key=:k
                    """
                ),
                {"c": str(c), "w": str(w), "k": key[:128], "b": barcode, "q": qty, "m": mode},
            )
            if updated.rowcount:
                continue
        db.execute(
            text(
                """
                INSERT INTO fc_pending_wb_receiving
                  (id, client_id, warehouse_id, barcode, quantity, mode, idempotency_key)
                VALUES (:id, :c, :w, :b, :q, :m, :k)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "c": str(c),
                "w": str(w),
                "b": barcode,
                "q": qty,
                "m": mode,
                "k": key[:128] if key else None,
            },
        )
    db.commit()
    return list_pending_wb(db, str(c), str(w))


def delete_pending_wb(
    db: Session,
    client_id: str,
    warehouse_id: str,
    *,
    idempotency_keys: list[str] | None = None,
    clear_all: bool = False,
) -> list[dict[str, Any]]:
    c, w = _uuid(client_id, "client_id"), _uuid(warehouse_id, "warehouse_id")
    if clear_all:
        db.execute(
            text("DELETE FROM fc_pending_wb_receiving WHERE client_id=:c AND warehouse_id=:w"),
            {"c": str(c), "w": str(w)},
        )
    elif idempotency_keys:
        for key in [str(k)[:128] for k in idempotency_keys if k]:
            db.execute(
                text(
                    """
                    DELETE FROM fc_pending_wb_receiving
                    WHERE client_id=:c AND warehouse_id=:w AND idempotency_key=:k
                    """
                ),
                {"c": str(c), "w": str(w), "k": key},
            )
    db.commit()
    return list_pending_wb(db, str(c), str(w))


def list_pending_ops(db: Session, scope_key: str | None = None, status: str = "PENDING") -> list[dict]:
    if scope_key:
        rows = db.execute(
            text(
                """
                SELECT id, client_id, warehouse_id, scope_key, kind, request_id, payload, status, created_at
                FROM fc_client_pending_ops
                WHERE scope_key=:s AND status=:st
                ORDER BY created_at ASC
                """
            ),
            {"s": str(scope_key)[:500], "st": status},
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                """
                SELECT id, client_id, warehouse_id, scope_key, kind, request_id, payload, status, created_at
                FROM fc_client_pending_ops
                WHERE status=:st
                ORDER BY created_at ASC
                LIMIT 500
                """
            ),
            {"st": status},
        ).mappings().all()
    out = []
    for r in rows:
        out.append(
            {
                "id": str(r["id"]),
                "client_id": str(r["client_id"]) if r["client_id"] else None,
                "warehouse_id": str(r["warehouse_id"]) if r["warehouse_id"] else None,
                "scope_key": r["scope_key"],
                "kind": r["kind"],
                "request_id": r["request_id"],
                "payload": r["payload"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
        )
    return out


def upsert_pending_op(
    db: Session,
    *,
    scope_key: str,
    kind: str,
    request_id: str,
    payload: dict,
    client_id: str | None = None,
    warehouse_id: str | None = None,
) -> dict:
    scope_key = str(scope_key or "").strip()[:500]
    kind = str(kind or "").strip()[:32]
    request_id = str(request_id or "").strip()[:96]
    if not scope_key or not kind or not request_id:
        raise HTTPException(status_code=422, detail="Нужны scope_key, kind и request_id")
    cid = str(_uuid(client_id, "client_id")) if client_id else None
    wid = str(_uuid(warehouse_id, "warehouse_id")) if warehouse_id else None
    existing = db.execute(
        text(
            "SELECT id FROM fc_client_pending_ops WHERE scope_key=:s AND request_id=:r"
        ),
        {"s": scope_key, "r": request_id},
    ).scalar()
    if existing:
        db.execute(
            text(
                """
                UPDATE fc_client_pending_ops
                SET payload=CAST(:p AS jsonb), kind=:k, status='PENDING',
                    client_id=COALESCE(:c, client_id), warehouse_id=COALESCE(:w, warehouse_id),
                    updated_at=now()
                WHERE id=:id
                """
            ),
            {
                "id": str(existing),
                "p": json.dumps(payload or {}, ensure_ascii=False),
                "k": kind,
                "c": cid,
                "w": wid,
            },
        )
        op_id = str(existing)
    else:
        op_id = str(uuid.uuid4())
        db.execute(
            text(
                """
                INSERT INTO fc_client_pending_ops
                  (id, client_id, warehouse_id, scope_key, kind, request_id, payload, status)
                VALUES (:id, :c, :w, :s, :k, :r, CAST(:p AS jsonb), 'PENDING')
                """
            ),
            {
                "id": op_id,
                "c": cid,
                "w": wid,
                "s": scope_key,
                "k": kind,
                "r": request_id,
                "p": json.dumps(payload or {}, ensure_ascii=False),
            },
        )
    db.commit()
    rows = list_pending_ops(db, scope_key)
    return next((x for x in rows if x["request_id"] == request_id), {"id": op_id})


def clear_pending_op(db: Session, scope_key: str, request_id: str | None = None) -> int:
    scope_key = str(scope_key or "").strip()[:500]
    if request_id:
        res = db.execute(
            text(
                """
                UPDATE fc_client_pending_ops
                SET status='DONE', updated_at=now()
                WHERE scope_key=:s AND request_id=:r AND status='PENDING'
                """
            ),
            {"s": scope_key, "r": str(request_id)[:96]},
        )
    else:
        res = db.execute(
            text(
                """
                UPDATE fc_client_pending_ops
                SET status='DONE', updated_at=now()
                WHERE scope_key=:s AND status='PENDING'
                """
            ),
            {"s": scope_key},
        )
    db.commit()
    return int(res.rowcount or 0)


def get_terminal_draft(db: Session, client_id: str, warehouse_id: str, terminal_key: str = "default") -> dict | None:
    c, w = _uuid(client_id, "client_id"), _uuid(warehouse_id, "warehouse_id")
    row = db.execute(
        text(
            """
            SELECT payload, updated_at FROM fc_terminal_drafts
            WHERE client_id=:c AND warehouse_id=:w AND terminal_key=:k
            """
        ),
        {"c": str(c), "w": str(w), "k": str(terminal_key)[:120]},
    ).mappings().first()
    if not row:
        return None
    return {
        "payload": row["payload"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def put_terminal_draft(
    db: Session, client_id: str, warehouse_id: str, payload: dict, terminal_key: str = "default"
) -> dict:
    c, w = _uuid(client_id, "client_id"), _uuid(warehouse_id, "warehouse_id")
    key = str(terminal_key or "default")[:120]
    db.execute(
        text(
            """
            INSERT INTO fc_terminal_drafts (id, client_id, warehouse_id, terminal_key, payload, updated_at)
            VALUES (:id, :c, :w, :k, CAST(:p AS jsonb), now())
            ON CONFLICT (client_id, warehouse_id, terminal_key)
            DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "c": str(c),
            "w": str(w),
            "k": key,
            "p": json.dumps(payload or {}, ensure_ascii=False),
        },
    )
    db.commit()
    return get_terminal_draft(db, str(c), str(w), key) or {"payload": payload}
