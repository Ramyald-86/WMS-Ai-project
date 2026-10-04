from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / "app" / "main.py").read_text(encoding="utf-8")
js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
mp = (root / "app" / "routers" / "marketplaces.py").read_text(encoding="utf-8")
layout = (root / "app" / "routers" / "layout.py").read_text(encoding="utf-8")
checks = {
    "version": 'version="0.7.9.4"' in main,
    "fast_product_burst_preserved": "Math.min(delay,85)" in js,
    "fast_chz_burst_preserved": "Math.min(delay,220)" in js,
    "batch_reportlab_canvas": "def _draw_location_label" in layout and "for location in rows:" in layout,
    "no_optional_pdf_merger": "from pypdf import" not in layout and "from PyPDF2 import" not in layout,
    "batch_print_single_job": 'document_type="LOCATION_QR_LABEL_BATCH"' in layout,
    "fresh_cancel_confirmation": "_refresh_exact_wb_status_for_label(db, client, order)" in mp,
    "wb_sgtin_delete": 'client.delete_meta(_wb_order_number(order), "sgtin")' in mp,
    "cancelled_release_event": "CHZ_AUTO_RELEASED_CANCELLED_BEFORE_LABEL" in mp,
    "release_only_cancelled_code": "WB_ORDER_CANCELLED_CHZ_RELEASED" in mp,
    "stock_rule_preserved": "PACKED_STAYS_WRITTEN_OFF" in mp,
    "browser_release_path": 'source="BROWSER_LABEL"' in mp and 'source="BROWSER_LABEL_PRECHECK"' in mp,
    "agent_release_path": 'source="PRINT_AGENT_LABEL"' in mp,
    "structured_api_errors": "error.status=r.status;error.data=data" in js and "error.status=r.status;error.data=d" in js,
    "background_release_message": "WB_ORDER_CANCELLED_CHZ_RELEASED" in js,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print("OK: FulfillCore 0.7.9.4 QR-batch/cancelled-CHZ smoke-check passed")
