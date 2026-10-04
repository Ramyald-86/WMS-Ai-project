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
runtime = app / "services" / "runtime_reliability.py"
registry = app / "services" / "wb_supply_registry.py"
js = app / "static" / "app.js"

for path, needle in [
    (main, 'version="0.7.9.7"'),
    (runtime, 'CREATE TABLE IF NOT EXISTS fc_wb_supply_registry'),
    (registry, 'def upsert_wb_supply('),
    (registry, 'def list_wb_supply_registry('),
    (market, 'def _wb_supply_add_confirmed('),
    (market, 'claim_runtime_interval('),
    (market, 'Пустую поставку WB нельзя закрыть'),
    (market, 'already_closed'),
    (market, 'created": True'),
    (js, "state.wbSupplyTimer=setInterval"),
    (js, "Добавить выбранные"),
    (js, "Создать пустую поставку WB?"),
    (js, "Закрываем…"),
    (js, "loadWbSupplies(false,true)"),
    # Cumulative guarantees from previous releases.
    (market, "PACKED_STAYS_WRITTEN_OFF"),
    (market, "CHZ_AUTO_RELEASED_CANCELLED_BEFORE_LABEL"),
    (js, "Math.min(delay,85)"),
    (js, "Math.min(delay,220)"),
    (js, "PALLET_PLACE"),
    (js, "SECTION"),
]:
    require(path, needle)

print("OK: FulfillCore 0.7.9.7 WB-supplies multi-device smoke-check passed")
