from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[2]

def test_chz_scanner_uses_stable_0949e_input_and_server_layout_recovery():
    js=(ROOT/'app/static/app.js').read_text(encoding='utf-8')
    py=(ROOT/'app/routers/marketplaces.py').read_text(encoding='utf-8')
    # 0.9.4.9e stable scanner: do not rebuild every character from KeyboardEvent.code.
    assert 'wbCaptureLayoutIndependentChzKey' not in js
    assert 'wbUsAsciiFromPhysicalKey' not in js
    assert 'function wbCaptureGsFromScanner(e)' in js
    assert 'layout_map' in py
    assert 'value = value.translate(layout_map)' in py

def test_api_status_not_coupled_to_commercial_session():
    js=(ROOT/'app/static/app.js').read_text(encoding='utf-8')
    assert "h=await api('/health'" in js
    assert "try{me=await api('/commercial/session'" in js
    assert "const [h,me]=await Promise.all([api('/health'),api('/commercial/session')])" not in js

def test_manual_receiving_verifies_exact_stock_bucket():
    py=(ROOT/'app/routers/receiving.py').read_text(encoding='utf-8')
    assert 'stock_verified' in py
    assert 'placements' in py
    assert 'commit=False' in py
    assert 'Container.warehouse_id == payload.warehouse_id' in py
    ast.parse(py)

def test_manual_receiving_rotates_idempotency_and_rejects_mismatched_retry():
    js=(ROOT/'app/static/app.js').read_text(encoding='utf-8')
    py=(ROOT/'app/routers/receiving.py').read_text(encoding='utf-8')
    # After server ACK the browser rotates via FC26 + server draft, not local-only save.
    assert 'FC26DraftStore.put(manualReceivingDraftKey(),newManualReceivingDraft())' in js
    assert 'fc27EnsureServerDraft' in js
    assert 'requested_signature != existing_signature' in py
    assert 'Ключ этой приёмки уже использован для другого состава товара' in py

def test_wb_optional_sgtin_decision_is_not_rejection():
    py=(ROOT/'app/routers/marketplaces.py').read_text(encoding='utf-8')
    assert '"filled", "optional"' in py
    assert '"pending", "required", "optional"' in py
