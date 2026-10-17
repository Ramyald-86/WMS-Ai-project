"""ZIPLINE OS modular permissions (TZ §1.3).

Legacy roles (PICKER/PACKER/…) remain as templates. The operational baseline is
OPERATOR with an explicit permission set. Platform admin gets all permissions.
"""

from __future__ import annotations

from typing import Any, Iterable

# Permission groups from TZ §1.3.1
PERMISSION_GROUPS = (
    "VIEW",
    "EXECUTE",
    "PRINT",
    "REPRINT",
    "CORRECT",
    "OVERRIDE",
    "EXPORT",
    "INTEGRATION_ADMIN",
    "ROLE_ADMIN",
)

# Atomic actions used by routers / UI
ACTIONS = (
    "VIEW_ORDER",
    "VIEW_STOCK",
    "VIEW_TASK",
    "VIEW_HISTORY",
    "VIEW_DOCS",
    "RECEIVE_START",
    "RECEIVE_SCAN",
    "PUTAWAY",
    "PICK_START",
    "PICK_SCAN",
    "PACK_START",
    "PACK_SCAN",
    "SHIP_CREATE",
    "SHIP_LOAD",
    "INVENTORY",
    "PRINT_LABEL",
    "REPRINT_LABEL",
    "CORRECT_STOCK",
    "OVERRIDE_VARIANCE",
    "EXPORT_REPORT",
    "INTEGRATION_DIAG",
    "ROLE_MANAGE",
    "FINANCE_VIEW",
    "FINANCE_CLOSE",
    "PLATFORM_SETTINGS",
)

ACTION_GROUP: dict[str, str] = {
    "VIEW_ORDER": "VIEW",
    "VIEW_STOCK": "VIEW",
    "VIEW_TASK": "VIEW",
    "VIEW_HISTORY": "VIEW",
    "VIEW_DOCS": "VIEW",
    "RECEIVE_START": "EXECUTE",
    "RECEIVE_SCAN": "EXECUTE",
    "PUTAWAY": "EXECUTE",
    "PICK_START": "EXECUTE",
    "PICK_SCAN": "EXECUTE",
    "PACK_START": "EXECUTE",
    "PACK_SCAN": "EXECUTE",
    "SHIP_CREATE": "EXECUTE",
    "SHIP_LOAD": "EXECUTE",
    "INVENTORY": "EXECUTE",
    "PRINT_LABEL": "PRINT",
    "REPRINT_LABEL": "REPRINT",
    "CORRECT_STOCK": "CORRECT",
    "OVERRIDE_VARIANCE": "OVERRIDE",
    "EXPORT_REPORT": "EXPORT",
    "INTEGRATION_DIAG": "INTEGRATION_ADMIN",
    "ROLE_MANAGE": "ROLE_ADMIN",
    "FINANCE_VIEW": "VIEW",
    "FINANCE_CLOSE": "CORRECT",
    "PLATFORM_SETTINGS": "ROLE_ADMIN",
}

# Module ids from frontend/shared/module-registry.js
MODULE_REQUIRED_ACTION: dict[str, str] = {
    "palletreceive": "RECEIVE_START",
    "receiving_terminal": "RECEIVE_START",
    "warehousework": "PICK_START",
    "warehouse_workflow": "PICK_START",
    "tsdflow": "PICK_START",
    "supplies": "PACK_START",
    "omnichannel": "PACK_START",
    "fbo": "SHIP_CREATE",
    "ship": "SHIP_CREATE",
    "inventoryplaces": "VIEW_STOCK",
    "stockmonitor": "VIEW_STOCK",
    "containers": "VIEW_STOCK",
    "storage": "VIEW_STOCK",
    "move": "PUTAWAY",
    "transit": "VIEW_STOCK",
    "defectstorage": "RECEIVE_START",
    "products": "VIEW_ORDER",
    "operations": "VIEW_HISTORY",
    "financeai": "FINANCE_VIEW",
    "financehistory": "FINANCE_VIEW",
    "connections": "INTEGRATION_DIAG",
    "printsettings": "PRINT_LABEL",
    "print_agent": "PRINT_LABEL",
    "staffroles": "ROLE_MANAGE",
    "platform_admin": "PLATFORM_SETTINGS",
    "seller_portal": "VIEW_ORDER",
    "network34": "VIEW_STOCK",
    "warehouse": "VIEW_TASK",
}

# Expanded role templates (TZ §1.3). Legacy roles kept for compatibility.
USER_ROLE_TEMPLATES = (
    "OPERATOR",
    "SUPERVISOR",
    "WAREHOUSE_MANAGER",
    "FC_ADMIN",
    "ACCOUNTANT",
    "PLATFORM_ADMIN",
    # legacy aliases
    "WAREHOUSE",
    "PICKER",
    "PACKER",
    "WB_OPERATOR",
    "VIEWER",
)

ROLE_DEFAULT_PERMISSIONS: dict[str, frozenset[str]] = {
    "OPERATOR": frozenset(
        {
            "VIEW_ORDER",
            "VIEW_STOCK",
            "VIEW_TASK",
            "VIEW_HISTORY",
            "RECEIVE_START",
            "RECEIVE_SCAN",
            "PUTAWAY",
            "PICK_START",
            "PICK_SCAN",
            "PACK_START",
            "PACK_SCAN",
            "SHIP_LOAD",
            "INVENTORY",
            "PRINT_LABEL",
        }
    ),
    "SUPERVISOR": frozenset(
        {
            "VIEW_ORDER",
            "VIEW_STOCK",
            "VIEW_TASK",
            "VIEW_HISTORY",
            "VIEW_DOCS",
            "RECEIVE_START",
            "RECEIVE_SCAN",
            "PUTAWAY",
            "PICK_START",
            "PICK_SCAN",
            "PACK_START",
            "PACK_SCAN",
            "SHIP_CREATE",
            "SHIP_LOAD",
            "INVENTORY",
            "PRINT_LABEL",
            "REPRINT_LABEL",
            "OVERRIDE_VARIANCE",
            "EXPORT_REPORT",
        }
    ),
    "WAREHOUSE_MANAGER": frozenset(ACTIONS) - frozenset({"PLATFORM_SETTINGS", "INTEGRATION_DIAG"}),
    "FC_ADMIN": frozenset(ACTIONS) - frozenset({"PLATFORM_SETTINGS"}),
    "ACCOUNTANT": frozenset(
        {"VIEW_ORDER", "VIEW_HISTORY", "VIEW_DOCS", "FINANCE_VIEW", "FINANCE_CLOSE", "EXPORT_REPORT"}
    ),
    "PLATFORM_ADMIN": frozenset(ACTIONS),
    "WAREHOUSE": frozenset(
        {
            "VIEW_ORDER",
            "VIEW_STOCK",
            "VIEW_TASK",
            "VIEW_HISTORY",
            "RECEIVE_START",
            "RECEIVE_SCAN",
            "PUTAWAY",
            "PICK_START",
            "PICK_SCAN",
            "PACK_START",
            "PACK_SCAN",
            "SHIP_LOAD",
            "INVENTORY",
            "PRINT_LABEL",
        }
    ),
    "PICKER": frozenset({"VIEW_ORDER", "VIEW_STOCK", "VIEW_TASK", "PICK_START", "PICK_SCAN", "PRINT_LABEL"}),
    "PACKER": frozenset({"VIEW_ORDER", "VIEW_TASK", "PACK_START", "PACK_SCAN", "PRINT_LABEL"}),
    "WB_OPERATOR": frozenset(
        {
            "VIEW_ORDER",
            "VIEW_TASK",
            "PACK_START",
            "PACK_SCAN",
            "SHIP_CREATE",
            "PRINT_LABEL",
            "REPRINT_LABEL",
            "INTEGRATION_DIAG",
            "EXPORT_REPORT",
        }
    ),
    "VIEWER": frozenset({"VIEW_ORDER", "VIEW_STOCK", "VIEW_TASK", "VIEW_HISTORY", "VIEW_DOCS"}),
}


def normalize_permissions(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        items = [x.strip() for x in raw.split(",") if x.strip()]
    elif isinstance(raw, dict):
        items = [str(k) for k, v in raw.items() if v]
    else:
        items = [str(x).strip() for x in raw if str(x).strip()]
    allowed = set(ACTIONS)
    return sorted({x for x in items if x in allowed})


def permissions_for_role(role: str | None, overrides: Any = None) -> list[str]:
    role_key = str(role or "VIEWER").strip().upper()
    base = set(ROLE_DEFAULT_PERMISSIONS.get(role_key, ROLE_DEFAULT_PERMISSIONS["VIEWER"]))
    if overrides is None or overrides == [] or overrides == {}:
        return sorted(base)
    # Explicit overrides replace the template when provided as a non-empty list.
    if isinstance(overrides, (list, tuple, set)):
        return normalize_permissions(overrides)
    if isinstance(overrides, dict):
        if overrides.get("__replace__"):
            return normalize_permissions(overrides.get("actions"))
        # Additive map: {action: true/false}
        for key, enabled in overrides.items():
            if key == "__replace__":
                continue
            action = str(key).strip().upper()
            if action not in ACTIONS:
                continue
            if enabled:
                base.add(action)
            else:
                base.discard(action)
    return sorted(base)


def has_permission(permissions: Iterable[str], action: str) -> bool:
    action = str(action or "").strip().upper()
    if not action:
        return False
    perms = {str(x).strip().upper() for x in permissions}
    if action in perms:
        return True
    # Group shortcut: granting a group grants all its actions
    group = ACTION_GROUP.get(action)
    if group and group in perms:
        return True
    return False


def modules_for_permissions(permissions: Iterable[str]) -> list[str]:
    return sorted(
        mid for mid, action in MODULE_REQUIRED_ACTION.items() if has_permission(permissions, action)
    )


def role_catalog() -> list[dict[str, Any]]:
    rows = []
    for role in USER_ROLE_TEMPLATES:
        if role == "PLATFORM_ADMIN":
            continue
        rows.append(
            {
                "role": role,
                "permissions": permissions_for_role(role),
                "is_legacy": role in {"WAREHOUSE", "PICKER", "PACKER", "WB_OPERATOR", "VIEWER"},
            }
        )
    return rows
