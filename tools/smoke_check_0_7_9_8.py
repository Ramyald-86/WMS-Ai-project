#!/usr/bin/env python3
from __future__ import annotations

import compileall
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = root / "app"


def require(path: Path, needle: str) -> None:
    value = path.read_text(encoding="utf-8-sig")
    if needle not in value:
        raise AssertionError(f"{path.relative_to(root)}: missing {needle!r}")


if not compileall.compile_dir(app, quiet=1, force=True):
    raise SystemExit("FAILED: Python compilation")

main = app / "main.py"
market = app / "routers" / "marketplaces.py"
registry = app / "services" / "wb_supply_registry.py"
js = app / "static" / "app.js"

for path, needle in [
    (main, 'version="0.7.9.8"'),
    (registry, 'strict_wb_warehouse_ids: bool = False'),
    (registry, 'if strict_wb_warehouse_ids:'),
    (market, 'strict_wb_warehouse_ids=wb_warehouse_id is not None'),
    (market, 'visible_wb_ids = {int(wb_warehouse_id)}'),
    (js, "wb_warehouse_id:String(selectedWh)"),
    (js, "Сначала выберите склад WB — поставки показываются только для выбранного склада."),
    (js, "Выберите конкретный склад WB — здесь будут только его поставки."),
    # Cumulative supply reliability guarantees from 0.7.9.7.
    (market, 'def _wb_supply_add_confirmed('),
    (market, 'claim_runtime_interval('),
    (market, 'Пустую поставку WB нельзя закрыть'),
    (js, 'Добавить выбранные'),
    (js, 'Закрываем…'),
    # Cumulative critical workflow guarantees.
    (market, 'PACKED_STAYS_WRITTEN_OFF'),
    (market, 'CHZ_AUTO_RELEASED_CANCELLED_BEFORE_LABEL'),
    (js, 'Math.min(delay,85)'),
    (js, 'Math.min(delay,220)'),
]:
    require(path, needle)

print("OK: FulfillCore 0.7.9.8 strict selected-WB-warehouse supplies smoke-check passed")
