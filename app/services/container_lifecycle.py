"""Automatic lifecycle for physical boxes.

A box is an active warehouse object only while it contains physical stock, or while it is
still a newly accepted empty box waiting to be filled. Once a previously stocked box is
fully consumed, it is archived automatically. Cells/pallet locations remain active.
"""
from __future__ import annotations

import json
import uuid
from typing import Iterable

from sqlalchemy import bindparam, text


_ACTIVE_PICK = ("DRAFT", "ASSIGNED", "IN_PROGRESS", "READY_FOR_ADMIN")
_ACTIVE_RESERVATION = ("ACTIVE", "CONSUMING", "CANCELLING", "PACKING_WRITE_OFF", "SALE_RESERVED")


def _ids(values: Iterable[object] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        raw = str(value or "").strip()
        if not raw:
            continue
        try:
            raw = str(uuid.UUID(raw))
        except (TypeError, ValueError):
            continue
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def archive_consumed_empty_containers(
    db,
    client_id,
    warehouse_id,
    container_ids: Iterable[object] | None = None,
    *,
    actor: str | None = None,
    reason: str = "AUTO_EMPTY_AFTER_STOCK_OUT",
) -> dict:
    """Archive empty boxes that previously contained stock.

    Safety rules:
    * never archive a box that still has positive physical stock;
    * never archive a box used by an open warehouse pick;
    * never archive a box with an active stock reservation;
    * never archive a brand-new empty box that has not yet received stock;
    * keep the location itself active.

    The caller owns the transaction and decides when to commit.
    """
    ids = _ids(container_ids)
    params = {"c": str(client_id), "w": str(warehouse_id)}
    id_clause = ""
    stmt = text(
        """
        SELECT CAST(c.id AS text) AS id, c.name, c.barcode, CAST(c.location_id AS text) AS location_id
        FROM containers c
        WHERE CAST(c.client_id AS text)=:c
          AND CAST(c.warehouse_id AS text)=:w
          AND c.status='ACTIVE'
          {id_clause}
          AND NOT EXISTS (
            SELECT 1 FROM inventory_balances b
            WHERE CAST(b.client_id AS text)=:c AND CAST(b.warehouse_id AS text)=:w
              AND CAST(b.container_id AS text)=CAST(c.id AS text)
              AND COALESCE(b.quantity,0)>0
          )
          AND EXISTS (
            SELECT 1
            FROM inventory_operation_lines l
            JOIN inventory_operations o ON o.id=l.operation_id
            WHERE CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
              AND o.status='COMPLETED'
              AND (
                    CAST(l.destination_container_id AS text)=CAST(c.id AS text)
                 OR CAST(l.source_container_id AS text)=CAST(c.id AS text)
              )
              AND COALESCE(l.quantity,0)>0
          )
          AND NOT EXISTS (
            SELECT 1
            FROM fc_wh_pick_lines l
            JOIN fc_wh_pick_tasks t ON t.id=l.task_id
            WHERE CAST(l.container_id AS text)=CAST(c.id AS text)
              AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN')
          )
          AND NOT EXISTS (
            SELECT 1 FROM fc_stock_reservations r
            WHERE CAST(r.source_container_id AS text)=CAST(c.id AS text)
              AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF','SALE_RESERVED')
              AND COALESCE(r.quantity,0)>0
          )
        ORDER BY c.id
        FOR UPDATE
        """.format(id_clause=("AND CAST(c.id AS text) IN :ids" if ids else ""))
    )
    if ids:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
        params["ids"] = ids
    rows = [dict(x) for x in db.execute(stmt, params).mappings().all()]
    if not rows:
        return {"archived": [], "count": 0}
    archived_ids = [x["id"] for x in rows]
    db.execute(
        text("UPDATE containers SET status='ARCHIVED' WHERE CAST(id AS text) IN :ids").bindparams(bindparam("ids", expanding=True)),
        {"ids": archived_ids},
    )

    # Best-effort box-intake audit. The main inventory ledger already keeps the stock-out
    # history; this extra row makes the automatic box disappearance understandable in the
    # receiving history when that optional audit table exists.
    try:
        # Optional history writes live in a savepoint. A legacy audit schema must never
        # poison the caller's main stock transaction in PostgreSQL.
        with db.begin_nested():
            has_audit = db.execute(text("SELECT to_regclass('public.fc_box_intake_audit')")).scalar()
            if has_audit:
                for row in rows:
                    receipt_id = db.execute(
                        text("SELECT receipt_id FROM fc_box_intake_boxes WHERE container_id=:b ORDER BY created_at DESC LIMIT 1"),
                        {"b": row["id"]},
                    ).scalar()
                    db.execute(
                        text(
                            """INSERT INTO fc_box_intake_audit(
                                   id,receipt_id,client_id,warehouse_id,action,actor,reason,before_json,after_json
                               ) VALUES(
                                   :id,:r,:c,:w,'BOX_AUTO_ARCHIVE_EMPTY',:actor,:reason,
                                   CAST(:before AS jsonb),CAST(:after AS jsonb)
                               )"""
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "r": receipt_id,
                            "c": str(client_id),
                            "w": str(warehouse_id),
                            "actor": str(actor or "")[:120],
                            "reason": str(reason or "")[:500],
                            "before": json.dumps({"status": "ACTIVE", "container_id": row["id"]}, ensure_ascii=False),
                            "after": json.dumps({"status": "ARCHIVED", "container_id": row["id"], "auto": True}, ensure_ascii=False),
                        },
                    )
    except Exception:
        # The savepoint above rolls back only the optional audit attempt. The inventory
        # transaction stays usable and the box lifecycle remains deterministic.
        pass
    return {"archived": rows, "count": len(rows)}
