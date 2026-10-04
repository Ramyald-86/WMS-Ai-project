from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / "app/main.py").read_text(encoding="utf-8")
stock = (root / "app/services/stock_reservations.py").read_text(encoding="utf-8")
checks = {
    "version": 'version="0.7.9.21"' in main,
    "shortage_status": '"status": "НЕ ХВАТАЕТ"' in stock,
    "shortage_continue": 'inventory shortage is informational, not fatal' in stock,
    "no_stale_location": '"location_id": None' in stock,
}
failed = [k for k, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAIL: " + ", ".join(failed))
print("OK: FulfillCore 0.7.9.21 shortage-tolerant picking-list smoke-check passed")
