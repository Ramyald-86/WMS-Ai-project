#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.8b FIFO PACKING EMERGENCY HOTFIX"


def function_bounds(src: str, name: str) -> tuple[int, int]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, node.end_lineno or node.lineno
    raise RuntimeError(f"function {name} not found")


def replace_function_keep_decorators(src: str, name: str, replacement: str) -> str:
    start, end = function_bounds(src, name)
    lines = src.splitlines(True)
    lines[start - 1:end] = [replacement.rstrip() + "\n\n"]
    out = "".join(lines)
    ast.parse(out)
    return out


PACKING_CREDENTIAL_HELPER = r'''
def _packing_credential_ids_for_scan(db: Session, cred: MarketplaceCredential) -> list[uuid.UUID]:
    """Credential lineage used only by the hot packing scanner.

    Display names are not an order identity. When this client/provider has exactly one
    active WB connection, older disconnected credential rows are treated as historical
    API-key generations of the same working cabinet for scanning only. We deliberately
    do NOT bulk-migrate historical orders here: the one order actually scanned is adopted
    by the current credential, keeping this hot path short and avoiding a large DB write.

    If several WB credentials are active for the same client, fall back to the existing
    same-name lineage so separate seller cabinets are never mixed automatically.
    """
    rows = db.execute(
        select(MarketplaceCredential).where(
            MarketplaceCredential.client_id == cred.client_id,
            MarketplaceCredential.provider == cred.provider,
        ).order_by(MarketplaceCredential.created_at.asc())
    ).scalars().all()
    active = [row for row in rows if bool(row.is_active)]
    if len(active) == 1 and active[0].id == cred.id:
        return [row.id for row in rows]
    ids = [row.id for row in rows if row.id == cred.id or row.name == cred.name]
    return ids or [cred.id]
'''


SCAN_PRODUCT = r'''def wb_scan_product(
    payload: WBScanProductRequest,
    station_code: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, payload.credential_id)
    barcode = payload.barcode.strip()
    if not barcode:
        raise HTTPException(status_code=400, detail="Пустой штрихкод товара")

    # 0.9.4.8b FIFO PACKING EMERGENCY HOTFIX
    # Reservation ownership must never decide which WB order is packed. The oldest
    # eligible order for the scanned SKU wins (NEW and PICKING), then its reservation
    # is healed/reassigned/created best-effort. This restores the warehouse rule
    # "первый зашел -> первый вышел" while preserving the strict CHZ->label->print gate.
    packing_credential_ids = _packing_credential_ids_for_scan(db, cred)
    base_stmt = select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(packing_credential_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.local_status.in_(["NEW", "PICKING"]),
    ).order_by(MarketplaceOrder.external_created_at.asc().nullsfirst(), MarketplaceOrder.created_at.asc())

    # Fast path: resolve the scanned local SKU once, while retaining a fallback for
    # historical orders that have no local_sku_id yet.
    matching_sku_ids = db.execute(
        select(SKU.id).where(
            SKU.client_id == cred.client_id,
            or_(SKU.barcode == barcode, SKU.article == barcode, SKU.sku_code == barcode),
        )
    ).scalars().all()

    def _load_candidates(direct: bool) -> list[MarketplaceOrder]:
        stmt = base_stmt
        if direct and matching_sku_ids:
            stmt = stmt.where(MarketplaceOrder.local_sku_id.in_(matching_sku_ids))
        rows = db.execute(stmt).scalars().all()
        rows = [o for o in rows if _wb_mode(cred, o.wb_warehouse_id, payload.warehouse_id) == "WORK"]

        # Stable sort: base SQL order is strict FIFO. Only an explicit operator "skip"
        # temporarily puts that order behind the untouched FIFO queue.
        def skip_key(o: MarketplaceOrder):
            raw = dict(o.raw_data or {})
            skipped_at = _parse_dt(raw.get("_fc_pack_skip_at"))
            return (1 if skipped_at else 0, skipped_at.timestamp() if skipped_at else 0.0)

        rows.sort(key=skip_key)
        return rows

    station = normalize_station_code(station_code) if station_code else None
    matched = None
    matching_but_busy = False
    checked_ids: set[uuid.UUID] = set()
    for direct in ((True, False) if matching_sku_ids else (False,)):
        for order in _load_candidates(direct):
            if order.id in checked_ids:
                continue
            checked_ids.add(order.id)
            is_match = barcode in (order.barcodes or [])
            if not is_match and order.local_sku_id:
                sku = db.get(SKU, order.local_sku_id)
                is_match = bool(sku and barcode in {sku.barcode, sku.article, sku.sku_code})
            if not is_match:
                continue
            if acquire_packing_lease(
                db,
                order_id=order.id,
                station_code=station,
                device_id=device_id,
                credential_id=cred.id,
                warehouse_id=payload.warehouse_id,
                barcode=barcode,
            ):
                matched = order
                break
            matching_but_busy = True
        if matched:
            break

    if not matched:
        if matching_but_busy:
            raise HTTPException(
                status_code=409,
                detail="Подходящий FIFO-заказ уже закреплён за другим упаковочным столом. Повторите сканирование — FulfillCore возьмёт следующий доступный заказ после освобождения/тайм-аута.",
            )

        receiving = db.execute(
            select(Location).where(
                Location.warehouse_id == payload.warehouse_id,
                or_(Location.code == "ПРИЕМКА", Location.barcode == "ПРИЕМКА"),
            )
        ).scalars().first()
        if receiving:
            virtual_rows = db.execute(
                select(InventoryBalance, SKU)
                .join(SKU, SKU.id == InventoryBalance.sku_id)
                .where(
                    InventoryBalance.client_id == cred.client_id,
                    InventoryBalance.warehouse_id == payload.warehouse_id,
                    InventoryBalance.location_id == receiving.id,
                    InventoryBalance.quantity > 0,
                    or_(SKU.barcode == barcode, SKU.article == barcode, SKU.sku_code == barcode),
                )
            ).all()
            if virtual_rows:
                qty = sum(float(balance.quantity or 0) for balance, _ in virtual_rows)
                sku = virtual_rows[0][1]
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ITEM_IN_VIRTUAL_RECEIVING",
                        "message": "Товар есть в виртуальной ПРИЕМКА, но активный WB-заказ NEW/На сборке с этим ШК не найден. При наличии заказа резерв больше не является условием для начала упаковки.",
                        "quantity": qty,
                        "sku_id": str(sku.id),
                        "article": sku.article or sku.sku_code,
                        "size": sku.size,
                        "barcode": sku.barcode,
                        "location_id": str(receiving.id),
                    },
                )
        raise HTTPException(status_code=404, detail="Нет активного заказа WB (Новый/На сборке) с таким штрихкодом. Проверьте ШК или синхронизируйте заказы WB.")

    # If the active API key replaced an older disconnected key, adopt only this scanned
    # order. The human-readable connection name is not used as the packing identity.
    if matched.credential_id != cred.id:
        old_credential_id = matched.credential_id
        matched.credential_id = cred.id
        _event(db, matched, "PACKING_ORDER_ADOPTED_BY_ACTIVE_WB_KEY", data={
            "from_credential_id": str(old_credential_id),
            "to_credential_id": str(cred.id),
        })

    # Bind a legacy order to an exact SKU when the scanned code has one unambiguous match.
    if not matched.local_sku_id and len(matching_sku_ids) == 1:
        matched.local_sku_id = matching_sku_ids[0]

    # Reservation is now a consequence of the FIFO-selected order, not a prerequisite.
    # Best effort order: existing -> legacy repair -> steal newer untouched reservation
    # -> reserve one truly free exact-SKU physical unit. Any remaining accounting gap is
    # audited but does not stop CHZ/label/physical packing.
    reservation = None
    reservation_error = None
    try:
        reservation = ensure_packing_reservation_for_scan(
            db,
            matched,
            payload.warehouse_id,
            eligible_credential_ids=packing_credential_ids,
        )
    except Exception as exc:
        reservation_error = str(exc)
        logger.exception(
            "FC_FIFO_PACK_RESERVATION_BEST_EFFORT_FAILED order_id=%s external=%s barcode=%s",
            matched.id,
            matched.external_order_num or matched.external_order_id,
            barcode,
        )

    if reservation:
        _event(db, matched, "PICKING_RESERVATION_READY", value=str(reservation.get("id") or ""), data={
            "status": reservation.get("status"),
            "source_location_id": reservation.get("source_location_id"),
            "source_container_id": reservation.get("source_container_id"),
            "fifo_auto": True,
        })
    else:
        _event(db, matched, "PACKING_FIFO_WITHOUT_RESERVATION", value=barcode, data={
            "allowed": True,
            "reason": reservation_error or "no_recoverable_reservation_or_free_stock",
            "stock_writeoff_will_be_best_effort_after_print": True,
        })

    # Product scan stays DB-only: no WB HTTP on this hot path.
    matched.product_scanned_barcode = barcode
    matched.product_scanned_at = _now()
    matched.local_status = "PICKING"
    _event(db, matched, "PRODUCT_SCANNED", value=barcode, data={
        "station_code": station,
        "device_id": device_id,
        "fifo": True,
        "reservation_id": str(reservation.get("id")) if reservation else None,
    })
    db.commit()
    return {"ok": True, "order": _packing_scan_order_response(db, matched, payload.warehouse_id), "next": "SCAN_CHZ"}
'''


PACKING_RESERVATION_HELPER = r'''
def ensure_packing_reservation_for_scan(
    db: Session,
    order: MarketplaceOrder,
    warehouse_id: Any,
    *,
    eligible_credential_ids: Iterable[Any] | None = None,
) -> dict[str, Any] | None:
    """Best-effort physical reservation for the order selected by packing FIFO.

    The scanner chooses the order first. This helper then tries to make stock accounting
    follow that decision without ever making a missing picking-list reservation a packing
    gate. It performs no marketplace HTTP requests.
    """
    ensure_stock_reservation_schema(db)
    if not order.local_sku_id or not warehouse_id:
        return None

    # Existing live/consumed reservation always wins and keeps the operation idempotent.
    latest = latest_reservations_for_orders(
        db, [order.id], statuses=["ACTIVE", "CONSUMING", "CONSUMED"]
    ).get(str(order.id))
    if latest:
        return latest

    # Preserve all old repair mechanisms for genuine picking-list orders before the
    # generic no-picking-list path below.
    if order.last_picking_list_id:
        latest = ensure_legacy_reservation_for_order(db, order)
        if not latest:
            latest = _fifo_reassign_newer_reservation_to_older_order(db, order)
        if not latest:
            latest = _recover_missing_reservation_from_free_stock(db, order)
        if latest:
            return latest

    allowed_credentials = {str(x) for x in (eligible_credential_ids or []) if x}
    target_time = order.external_created_at or order.created_at
    lock_key = f"packing-scan-fifo-reserve:{order.client_id}:{warehouse_id}:{order.local_sku_id}"

    with distributed_lock(db, lock_key, blocking=False) as acquired:
        if not acquired:
            return None

        # Another packing request may have healed the order while we waited for the lock.
        latest = latest_reservations_for_orders(
            db, [order.id], statuses=["ACTIVE", "CONSUMING", "CONSUMED"]
        ).get(str(order.id))
        if latest:
            return latest

        # FIFO repair: a newer untouched order may own the only reserved unit. Move that
        # reservation to the older scanned order; physical location and total reserve stay
        # unchanged. This is the key rule: reservation follows FIFO, never the reverse.
        try:
            with db.begin_nested():
                donors = db.execute(
                    text(
                        """
                        SELECT id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                               sku_id,source_location_id,source_container_id,quantity,status,created_at,
                               updated_at,consumed_at,receiving_at,released_at,note
                        FROM fc_stock_reservations
                        WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
                          AND status='ACTIVE' AND order_id<>:target
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        """
                    ),
                    {
                        "client": str(order.client_id),
                        "warehouse": str(warehouse_id),
                        "sku": str(order.local_sku_id),
                        "target": str(order.id),
                    },
                ).mappings().all()

                for row in donors:
                    try:
                        donor = db.get(MarketplaceOrder, uuid.UUID(str(row["order_id"])))
                    except Exception:
                        donor = None
                    if not donor or str(donor.provider or "").upper() != "WB":
                        continue
                    if allowed_credentials and str(donor.credential_id) not in allowed_credentials:
                        continue
                    if str(donor.local_status or "").upper() not in {"NEW", "PICKING"}:
                        continue
                    if donor.product_scanned_at or donor.chz_code or donor.packed_at:
                        continue
                    donor_time = donor.external_created_at or donor.created_at
                    if target_time and donor_time and donor_time <= target_time:
                        continue

                    target_core_id = ensure_canonical_marketplace_order(
                        db, order, warehouse_id, order.local_sku_id
                    )
                    result = db.execute(
                        text(
                            """
                            UPDATE fc_stock_reservations
                            SET order_id=:target,external_order_id=:external,picking_list_id=:picking,
                                stock_core_order_id=:stock_core_order_id,
                                updated_at=CURRENT_TIMESTAMP,note='FIFO_PACKING_REASSIGNED_TO_OLDER_ORDER_0_9_4_8b'
                            WHERE id=:id AND status='ACTIVE' AND order_id=:donor
                            """
                        ),
                        {
                            "target": str(order.id),
                            "external": str(order.external_order_id or order.external_order_num or "")[:64],
                            "picking": str(order.last_picking_list_id) if order.last_picking_list_id else None,
                            "stock_core_order_id": target_core_id,
                            "id": str(row["id"]),
                            "donor": str(donor.id),
                        },
                    )
                    if result.rowcount:
                        db.execute(
                            text("UPDATE fc_orders_v2 SET status='NEW' WHERE source_order_id=:source AND status='RESERVED'"),
                            {"source": str(donor.id)},
                        )
                        db.execute(
                            text("UPDATE fc_orders_v2 SET status='RESERVED' WHERE id=:id"),
                            {"id": target_core_id},
                        )
                        healed = dict(row)
                        healed.update({
                            "order_id": str(order.id),
                            "external_order_id": str(order.external_order_id or order.external_order_num or "")[:64],
                            "picking_list_id": str(order.last_picking_list_id) if order.last_picking_list_id else None,
                            "stock_core_order_id": target_core_id,
                            "note": "FIFO_PACKING_REASSIGNED_TO_OLDER_ORDER_0_9_4_8b",
                        })
                        return healed
        except Exception:
            # A savepoint prevents a contested donor row/deadlock from poisoning the
            # scanner transaction. We can still try a genuinely free physical unit.
            pass

        # No donor reservation: create a real Stock Core v2 reservation from one free
        # exact-SKU physical unit. Real addresses first; virtual ПРИЕМКА only last.
        rows = db.execute(
            select(InventoryBalance, Location)
            .join(Location, Location.id == InventoryBalance.location_id)
            .where(
                InventoryBalance.client_id == order.client_id,
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.sku_id == order.local_sku_id,
                InventoryBalance.quantity > 0,
            )
            .with_for_update(of=InventoryBalance, skip_locked=True)
        ).all()
        rows.sort(key=lambda pair: (1 if str(pair[1].code or "").strip().upper() == "ПРИЕМКА" else 0, str(pair[1].code or "")))

        for balance, location in rows:
            code = str(location.code or "").strip().upper()
            if code in SYSTEM_LOCATION_CODES and code != "ПРИЕМКА":
                continue
            reserved = live_reserved_for_bucket(
                db,
                client_id=order.client_id,
                warehouse_id=warehouse_id,
                sku_id=order.local_sku_id,
                location_id=balance.location_id,
                container_id=balance.container_id,
            )
            if _d(balance.quantity) - reserved < Decimal(1) - EPSILON:
                continue

            try:
                with db.begin_nested():
                    rid = str(uuid.uuid4())
                    inserted = db.execute(
                        text(
                            """
                            INSERT INTO fc_stock_reservations(
                                id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                                sku_id,source_location_id,source_container_id,quantity,status,note
                            ) VALUES(
                                :id,:order_id,:external,:picking,:client,:warehouse,
                                :sku,:location,:container,1,'CONSUMING','PACKING_FIFO_AUTO_RESERVE_0_9_4_8b'
                            )
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        {
                            "id": rid,
                            "order_id": str(order.id),
                            "external": str(order.external_order_num or order.external_order_id or "")[:64],
                            "picking": str(order.last_picking_list_id) if order.last_picking_list_id else None,
                            "client": str(order.client_id),
                            "warehouse": str(warehouse_id),
                            "sku": str(order.local_sku_id),
                            "location": str(balance.location_id),
                            "container": str(balance.container_id) if balance.container_id else None,
                        },
                    )
                    if not inserted.rowcount:
                        continue
                    reserve_marketplace_unit(
                        db,
                        order=order,
                        warehouse_id=warehouse_id,
                        sku_id=order.local_sku_id,
                        location_id=balance.location_id,
                        container_id=balance.container_id,
                        reservation_id=rid,
                    )
                recovered = latest_reservations_for_orders(
                    db, [order.id], statuses=["ACTIVE", "CONSUMING", "CONSUMED"]
                ).get(str(order.id))
                if recovered:
                    return recovered
            except Exception:
                continue

    return None
'''


CONSUME_ORDER = r'''def consume_order_reservation(db: Session, order: MarketplaceOrder, *, note: str = "WB_PACKED") -> dict[str, Any]:
    """Final FBS write-off, exactly once, including packing-time reservations.

    0.9.4.8b: a picking-list id is no longer a prerequisite. If a real reservation
    exists, consume it; if none exists, report no_reservation and let the strict print
    finalizer audit that accounting gap without freezing the packing table.
    """
    ensure_stock_reservation_schema(db)
    with distributed_lock(db, f"stock-reservation-order:{order.id}", blocking=True):
        latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
        if not latest and order.last_picking_list_id:
            latest = ensure_legacy_reservation_for_order(db, order)
        if not latest:
            return {"changed": False, "reason": "no_reservation"}
        status = str(latest.get("status") or "").upper()
        if status == "CONSUMED":
            return {"changed": False, "reason": "already_consumed", "reservation": latest}
        if status not in {"ACTIVE", "CONSUMING"}:
            return {"changed": False, "reason": f"status_{status.lower()}", "reservation": latest}
        return consume_marketplace_reservation(db, order=order, reservation=latest, note=note)
'''


def patch_marketplaces(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    ast.parse(src)
    if MARKER in src:
        print("OK: marketplaces.py already 0.9.4.8b")
        return
    for required in (
        "0.9.4.8 REV2 SEQUENTIAL CONFIRMED PRINT",
        "0.9.4.8a ROUTE RESTORE HOTFIX",
        "def wb_scan_product(",
        "def _finalize_strict_pack_from_job(",
    ):
        if required not in src:
            raise RuntimeError(f"required live marker missing: {required}")

    # Import one new best-effort reservation helper.
    old_import = "    assert_order_reservation_ready_for_packing,\n"
    if old_import not in src:
        raise RuntimeError("stock_reservations import anchor missing")
    src = src.replace(old_import, old_import + "    ensure_packing_reservation_for_scan,\n", 1)

    # Add scanner-only credential lineage helper without changing global credential merge.
    anchor = "\ndef _merge_order_history(db: Session, target: MarketplaceOrder, source: MarketplaceOrder) -> None:\n"
    if anchor not in src:
        raise RuntimeError("credential helper insertion anchor missing")
    src = src.replace(anchor, "\n" + PACKING_CREDENTIAL_HELPER.strip() + "\n\n" + anchor.lstrip("\n"), 1)

    src = replace_function_keep_decorators(src, "wb_scan_product", SCAN_PRODUCT)

    # Duplicate CHZ protection must also see orders that were imported under an older
    # disconnected key of this same single-active WB connection.
    duplicate_old = """    duplicate = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id == payload.credential_id,
        MarketplaceOrder.chz_code == chz,
        MarketplaceOrder.id != order.id,
    )).scalars().first()
"""
    duplicate_new = """    duplicate = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(_packing_credential_ids_for_scan(db, cred)),
        MarketplaceOrder.chz_code == chz,
        MarketplaceOrder.id != order.id,
    )).scalars().first()
"""
    if duplicate_old not in src:
        raise RuntimeError("scan-chz duplicate protection anchor missing")
    src = src.replace(duplicate_old, duplicate_new, 1)

    # A physically successful label is the operational boundary. Missing reservation is
    # audited, but it must not leave the order forever stuck after the label is printed.
    finalizer_old = '_consume_picking_reservation_strict(db2, order, "WB_PACKED_AFTER_PRINT_ACK")'
    finalizer_new = '_consume_picking_reservation_strict(db2, order, "WB_PACKED_AFTER_PRINT_ACK", require=False)'
    if finalizer_old not in src:
        raise RuntimeError("strict finalizer write-off anchor missing")
    src = src.replace(finalizer_old, finalizer_new, 1)

    marker_anchor = "# 0.9.4.8a ROUTE RESTORE HOTFIX"
    src = src.replace(marker_anchor, marker_anchor + f"\n# {MARKER}", 1)
    ast.parse(src)
    path.write_text(src, encoding="utf-8")
    print("OK: marketplaces FIFO NEW/PICKING scan + nonblocking reservation gate patched")


def patch_reservations(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    ast.parse(src)
    if "def ensure_packing_reservation_for_scan(" in src and "PACKING_FIFO_AUTO_RESERVE_0_9_4_8b" in src:
        print("OK: stock_reservations.py already 0.9.4.8b")
        return

    # Canonical order bookkeeping follows a reassigned reservation as well.
    old = "    consume_marketplace_reservation, ensure_stock_core_v2_schema,\n    release_or_restore_marketplace_reservation, reserve_marketplace_unit,\n"
    new = "    consume_marketplace_reservation, ensure_canonical_marketplace_order, ensure_stock_core_v2_schema,\n    release_or_restore_marketplace_reservation, reserve_marketplace_unit,\n"
    if old not in src:
        raise RuntimeError("stock_core_v2 import block anchor missing")
    src = src.replace(old, new, 1)

    anchor = "\ndef assert_order_reservation_ready_for_packing(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:\n"
    if anchor not in src:
        raise RuntimeError("assert reservation insertion anchor missing")
    src = src.replace(anchor, "\n" + PACKING_RESERVATION_HELPER.strip() + "\n\n" + anchor.lstrip("\n"), 1)
    src = replace_function_keep_decorators(src, "consume_order_reservation", CONSUME_ORDER)
    ast.parse(src)
    path.write_text(src, encoding="utf-8")
    print("OK: stock_reservations packing-time FIFO reservation helper patched")


def patch_main(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    ast.parse(src)
    if 'version="0.9.4.8b"' in src:
        print("OK: main.py already 0.9.4.8b")
        return
    out, n = re.subn(r'version="0\.9\.4\.8a"', 'version="0.9.4.8b"', src, count=1)
    if n != 1:
        raise RuntimeError('FastAPI version="0.9.4.8a" not found exactly once')
    ast.parse(out)
    path.write_text(out, encoding="utf-8")
    print("OK: health version -> 0.9.4.8b")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    args = ap.parse_args()
    root = Path(args.root)
    patch_marketplaces(root / "app/routers/marketplaces.py")
    patch_reservations(root / "app/services/stock_reservations.py")
    patch_main(root / "app/main.py")


if __name__ == "__main__":
    main()
