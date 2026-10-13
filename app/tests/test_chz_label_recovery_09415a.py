from __future__ import annotations

import ast
import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[2]
MARKETPLACES = ROOT / "app" / "routers" / "marketplaces.py"
MAIN = ROOT / "app" / "main.py"


class ChzLabelRecovery09415aTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MARKETPLACES.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def function_source(self, name: str) -> str:
        node = next(
            item
            for item in self.tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
        )
        return ast.get_source_segment(self.source, node) or ""

    def test_python_and_version_are_valid(self) -> None:
        ast.parse(MAIN.read_text(encoding="utf-8"))
        self.assertIn('version="0.9.4.15g5a8k9r145f54"', MAIN.read_text(encoding="utf-8"))

    def test_repeated_409_keeps_proven_0949e_pipeline(self) -> None:
        worker = self.function_source("_schedule_strict_pack_gate")
        self.assertIn("FC_PACK_FAST_SGTIN_409_CONTINUE", worker)
        self.assertIn("only an explicit reject is fatal", worker)
        self.assertNotIn("SGTIN_409_UNKNOWN", worker)
        self.assertNotIn("Печать остановлена, чтобы не упаковать", worker)

    def test_sticker_decoder_handles_real_wb_variants(self) -> None:
        node = next(
            item
            for item in self.tree.body
            if isinstance(item, ast.FunctionDef) and item.name == "_decode_wb_sticker_file"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace: dict[str, Any] = {"Any": Any, "base64": base64}
        exec(compile(module, str(MARKETPLACES), "exec"), namespace)
        decode = namespace["_decode_wb_sticker_file"]

        image = b"\x89PNG\r\n\x1a\nFULFILLCORE"
        encoded = base64.b64encode(image).decode("ascii")
        self.assertEqual(decode(encoded), image)
        self.assertEqual(decode(encoded.rstrip("=")), image)
        self.assertEqual(decode(f"data:image/png;base64,{encoded}"), image)
        self.assertEqual(decode(f"  {encoded[:8]}\n{encoded[8:]}  "), image)
        self.assertIsNone(decode("%%%"))
        self.assertIsNone(decode(None))

    def test_bulk_reprint_cannot_leak_base64_as_http_500(self) -> None:
        endpoint = self.function_source("wb_order_labels_pdf")
        self.assertIn("_decode_wb_sticker_file", endpoint)
        self.assertIn("BULK_BROWSER_LABEL_RECOVERY", endpoint)
        self.assertIn("status_code=503", endpoint)
        self.assertNotIn('base64.b64decode(item["file"])', endpoint)
        self.assertNotIn('order.local_status = "SENT"', endpoint)

    def test_successful_bulk_reprint_packs_picking_even_without_rescan(self) -> None:
        endpoint = self.function_source("wb_order_labels_pdf")
        self.assertIn('packed_from_picking = order.local_status == "PICKING"', endpoint)
        self.assertIn('if packed_from_picking:', endpoint)
        self.assertIn('WB_PACKED_BULK_LABEL_OPERATOR', endpoint)
        self.assertIn('_set_manual_workflow_status(order, "PACKED")', endpoint)
        self.assertIn('order.local_status = "PACKED"', endpoint)
        self.assertNotIn('if order.product_scanned_at:', endpoint)
        self.assertIn('product_scan_present', endpoint)

    def test_completed_print_gate_cannot_fall_back_to_picking(self) -> None:
        names = {"_compute_local_status", "_picking_has_chz_and_label"}
        nodes = [
            item
            for item in self.tree.body
            if isinstance(item, ast.FunctionDef) and item.name in names
        ]
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        namespace: dict[str, Any] = {
            "MarketplaceOrder": Any,
            "WB_PHYSICALLY_LEFT_STATUSES": {"sorted"},
            "_wb_order_currently_cancelled": lambda order: False,
            "_has_pending_wb_chz": lambda order: False,
            "_has_wb_order_sticker": lambda order: False,
        }
        exec(compile(module, str(MARKETPLACES), "exec"), namespace)
        compute = namespace["_compute_local_status"]
        order = SimpleNamespace(
            supplier_status="confirm",
            wb_status="",
            raw_data={"_fc_pack_gate_v1": {"stage": "DONE", "printed_at": "2026-09-02T20:00:00"}},
            packed_at="2026-09-02T20:00:00",
            product_scanned_at=None,
            chz_code="010460000000000021ABC",
            local_status="PICKING",
            manual_status_override=False,
            picking_list_generated_at="2026-09-02T19:50:00",
        )
        self.assertEqual(compute(order), "PACKED")

        order.raw_data = {"_fc_pack_gate_v1": {"stage": "PRINT_QUEUED"}}
        self.assertEqual(compute(order), "PICKING")

        order.raw_data = {"_fc_pack_gate_v1": {"stage": "DONE"}}
        order.wb_status = "sorted"
        self.assertEqual(compute(order), "SENT")

    def test_stock_accounting_is_not_a_packing_gate(self) -> None:
        wrapper = self.function_source("_consume_picking_reservation_nonblocking")
        manual = self.function_source("wb_manual_workflow_status")
        finalizer = self.function_source("_finalize_strict_pack_from_job")
        self.assertIn("db.begin_nested()", wrapper)
        self.assertIn("PACK_STOCK_RECONCILIATION_PENDING", wrapper)
        self.assertIn("_consume_picking_reservation_nonblocking", manual)
        self.assertIn("_consume_picking_reservation_nonblocking", finalizer)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ChzLabelRecovery09415aTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("REGRESSION_OK 0.9.4.15a CHZ_LABEL_RECOVERY")
