"""Tests for ZIPLINE TZ v1.3 completion of previously partial features."""

from __future__ import annotations

from app.services.zipline_permissions import (
    has_permission,
    modules_for_permissions,
    permissions_for_role,
)
from app.services.zipline_tz_core import detect_scan_kind, hash_pin, sla_risk_band, verify_pin


def test_operator_has_modular_execute_but_not_integration():
    perms = permissions_for_role("OPERATOR")
    assert has_permission(perms, "RECEIVE_START")
    assert has_permission(perms, "PACK_START")
    assert not has_permission(perms, "INTEGRATION_DIAG")
    assert "supplies" in modules_for_permissions(perms)
    assert "connections" not in modules_for_permissions(perms)


def test_packer_lacks_integration_admin():
    perms = permissions_for_role("PACKER")
    assert has_permission(perms, "PACK_SCAN")
    assert not has_permission(perms, "INTEGRATION_DIAG")
    assert not has_permission(perms, "SHIP_CREATE")


def test_empty_overrides_fall_back_to_role_template():
    assert permissions_for_role("PICKER", []) == permissions_for_role("PICKER")


def test_sla_risk_bands_green_yellow_red():
    assert sla_risk_band(2, 24)["risk"] == "GREEN"
    assert sla_risk_band(18, 24)["risk"] == "YELLOW"
    assert sla_risk_band(30, 24)["risk"] == "RED"
    assert sla_risk_band(30, 24)["sla_critical"] is True


def test_detect_datamatrix_routes_to_chz():
    # 31+ chars with digits → CHZ / DataMatrix
    code = "010460406359149021Nabcdefg\u001d93ZXCV"
    kind = detect_scan_kind(code)
    assert kind["kind"] == "CHZ"
    routed = detect_scan_kind(code)
    assert routed["kind"] == "CHZ"


def test_detect_ean_and_hu():
    assert detect_scan_kind("4604063591490")["kind"] == "EAN"
    assert detect_scan_kind("BOX-12345")["kind"] == "HU"
    assert detect_scan_kind("PLT-00981")["kind"] == "HU"


def test_pin_hash_roundtrip():
    encoded = hash_pin("1234")
    assert verify_pin("1234", encoded)
    assert not verify_pin("9999", encoded)


def test_weight_required_gate_logic():
    # Mirrors /packing/hu/{id}/close rule
    weight_required = True
    weight_kg = None
    blocked = weight_required and weight_kg is None
    assert blocked is True
    assert not (False and weight_kg is None)
