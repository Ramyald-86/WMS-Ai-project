from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import httpx
from fastapi import HTTPException

from app.routers import receiving as rc
from app.routers.marketplaces import _local_wb_supply_barcode_png
from app.services.printing import _print_payload_type


class FakeWbStockClient:
    def __init__(self, apply_before_timeout: bool):
        self.apply_before_timeout = apply_before_timeout
        self.state: dict[int, int] = {}
        self.puts = 0
        self.reads = 0

    def _put(self, path: str, json: dict):
        self.puts += 1
        updates = {int(x["chrtId"]): int(x["amount"]) for x in json.get("stocks", [])}
        if self.puts == 1 and self.apply_before_timeout:
            self.state.update(updates)
            raise HTTPException(status_code=504, detail="lost response")
        if self.puts == 1 and not self.apply_before_timeout:
            raise HTTPException(status_code=504, detail="lost response")
        self.state.update(updates)
        return httpx.Response(204, request=httpx.Request("PUT", "https://example.invalid" + path))

    def _post_read(self, path: str, json: dict):
        self.reads += 1
        rows = [{"chrtId": cid, "amount": self.state.get(int(cid), 0)} for cid in json.get("chrtIds", [])]
        return httpx.Response(200, json={"stocks": rows}, request=httpx.Request("POST", "https://example.invalid" + path))


def test_wb_unknown_write_is_reconciled_before_any_retry(monkeypatch) -> None:
    monkeypatch.setattr(rc.time, "sleep", lambda _seconds: None)
    client = FakeWbStockClient(apply_before_timeout=True)
    observed = rc._wb_write_and_verify_chrt_stocks(client, 123, {77: 12})
    assert observed[77] == 12
    assert client.puts == 1
    assert client.reads >= 1


def test_wb_retry_happens_only_after_read_proves_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(rc.time, "sleep", lambda _seconds: None)
    client = FakeWbStockClient(apply_before_timeout=False)
    observed = rc._wb_write_and_verify_chrt_stocks(client, 123, {77: 12})
    assert observed[77] == 12
    assert client.puts == 2
    assert client.reads >= 2


class FakeContentClient:
    def __init__(self):
        self.calls: list[tuple[str | None, int]] = []

    def content_cards(self, text_search=None, limit_total=100):
        self.calls.append((text_search, limit_total))
        if text_search is None:
            return []
        code = str(text_search)
        return [{"sizes": [{"chrtID": int(code[-3:]) + 1000, "skus": [code]}]}]


def test_small_catalogue_recovery_uses_exact_search_not_full_scan() -> None:
    client = FakeContentClient()
    result = rc._wb_fresh_chrt_map(client, {"2043110552746", "2043110552123"})
    assert set(result) == {"2043110552746", "2043110552123"}
    assert len(client.calls) == 2
    assert all(search is not None and limit == 100 for search, limit in client.calls)


def test_supply_qr_is_real_png_and_mime_cannot_be_stale_pdf() -> None:
    raw = _local_wb_supply_barcode_png("WB-GI-266105063")
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert _print_payload_type(raw, "wrong.pdf", "application/pdf") == "image/png"
