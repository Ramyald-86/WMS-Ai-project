from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / "app" / "main.py").read_text(encoding="utf-8")
js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
mp = (root / "app" / "routers" / "marketplaces.py").read_text(encoding="utf-8")
checks = {
    "version": 'version="0.7.9.3"' in main,
    "compact_product_response": "_packing_scan_order_response" in mp,
    "direct_sku_fast_path": "matching_sku_ids" in mp,
    "fast_product_burst": "Math.min(delay,85)" in js,
    "fast_chz_burst": "Math.min(delay,220)" in js,
    "tab_submit": "e.key==='Tab'" in js,
    "background_label": "queueWbBackgroundLabel" in js,
    "background_refresh": "scheduleWbHotRefresh" in js,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print("OK: FulfillCore 0.7.9.3 scan-throughput smoke-check passed")
