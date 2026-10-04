#!/usr/bin/env python3
from __future__ import annotations

import compileall
import io
from pathlib import Path
from openpyxl import Workbook

root = Path(__file__).resolve().parents[1]
app = root / "app"


def require(path: Path, needle: str) -> None:
    value = path.read_text(encoding="utf-8-sig")
    if needle not in value:
        raise AssertionError(f"{path.relative_to(root)}: missing {needle!r}")


if not compileall.compile_dir(app, quiet=1, force=True):
    raise SystemExit("FAILED: Python compilation")

main = app / "main.py"
receiving = app / "routers" / "receiving.py"
runtime = app / "services" / "runtime_reliability.py"
parser = app / "services" / "structured_receiving.py"
js = app / "static" / "app.js"
migration = app / "migrations" / "0_7_9_5_structured_receiving.sql"
market = app / "routers" / "marketplaces.py"
layout = app / "routers" / "layout.py"

for path, needle in [
    (main, 'version="0.7.9.5"'),
    (parser, "def parse_structured_workbook"),
    (parser, 'return f"Секция {str(section).strip()} · Палета {str(pallet).strip()}"'),
    (runtime, "CREATE TABLE IF NOT EXISTS fc_structured_receipts"),
    (migration, "CREATE TABLE IF NOT EXISTS fc_structured_receipt_rows"),
    (receiving, '@router.post("/structured-receipts")'),
    (receiving, '@router.post("/structured-receipts/{receipt_id}/commit")'),
    (receiving, 'idempotency_key=f"structured-receipt-{receipt_id}"'),
    (receiving, 'location_type="PALLET"'),
    (js, "1. Загрузить и проверить"),
    (js, "2. Добавить на остатки"),
    (js, "matchingBoxLocationIds"),
    (js, "renderStructuredBoxContents"),
    (receiving, '"items": [],'),
    # Critical previous guarantees remain in the cumulative patch.
    (market, "PACKED_STAYS_WRITTEN_OFF"),
    (market, "CHZ_AUTO_RELEASED_CANCELLED_BEFORE_LABEL"),
    (layout, 'document_type="LOCATION_QR_LABEL_BATCH"'),
    (js, "Math.min(delay,85)"),
    (js, "Math.min(delay,220)"),
]:
    require(path, needle)

# Parser functional check, including an empty box and a zero-quantity product row.
import sys
sys.path.insert(0, str(root))
from app.services.structured_receiving import parse_structured_workbook

wb = Workbook()
ws = wb.active
ws.title = "Адресное хранение"
headers = ["Секция","Паллета","Лимит","Короб","Партия","Источник","Принят на хранение","Дней","Объём м3","Начислено руб","Артикул","Размер","ШК товара","Остаток"]
ws.append(headers)
ws.append([1,1,16,"BOX-001","P1","TEST","2026-08-28",0,0,0,"A-1","42","460000000001",3])
ws.append([1,1,16,"BOX-EMPTY","P1","TEST","2026-08-28",0,0,0,"","","",""])
ws.append([1,1,16,"BOX-ZERO","P1","TEST","2026-08-28",0,0,0,"A-2","44","460000000002",0])
buf = io.BytesIO(); wb.save(buf)
rows, summary = parse_structured_workbook(buf.getvalue())
assert summary["section_count"] == 1
assert summary["pallet_count"] == 1
assert summary["box_count"] == 3
assert summary["product_row_count"] == 1
assert summary["empty_box_count"] == 1
assert summary["zero_product_row_count"] == 1
assert summary["quantity_total"] == 3.0
assert summary["error_count"] == 0
assert summary["warning_count"] == 1
assert len(rows) == 3

print("OK: FulfillCore 0.7.9.5 structured Excel receiving smoke-check passed")
