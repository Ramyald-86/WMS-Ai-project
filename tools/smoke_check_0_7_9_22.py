from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / "app" / "main.py").read_text(encoding="utf-8")
reservations = (root / "app" / "services" / "stock_reservations.py").read_text(encoding="utf-8")
checks = {
    "version": 'version="0.7.9.22"' in main,
    "new reservation transient": "1, 'CONSUMING', :note" in reservations,
    "old reservation transient": "status='CONSUMING'" in reservations and "WB_PICKING_LIST_COMMITTING" in reservations,
    "shortage remains nonblocking": 'allocation.update({' in reservations and '"status": "НЕ ХВАТАЕТ"' in reservations,
    "commit at picking list": 'reason="WB_PICKING_LIST_STOCK_COMMIT"' in reservations,
    "finish consumed": "status='CONSUMED'" in reservations,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAIL: " + ", ".join(failed))
print("OK: FulfillCore 0.7.9.22 picking-list self-reservation guard fix smoke-check passed")
