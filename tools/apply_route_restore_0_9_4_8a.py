#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.8a ROUTE RESTORE HOTFIX"

ROUTES = [
    ("wb_scan_product", "post", "/wb/packing/scan-product"),
    ("wb_scan_chz", "post", "/wb/packing/scan-chz"),
    ("wb_packing_progress", "get", "/wb/packing/progress"),
    ("wb_queue_order_label", "post", "/wb/orders/{order_id}/label/queue"),
]


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


def _function(src: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(_tree(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise RuntimeError(f"function {name} not found")


def _route_matches(node: ast.FunctionDef | ast.AsyncFunctionDef, method: str, path: str) -> bool:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        fn = dec.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == method):
            continue
        if not (isinstance(fn.value, ast.Name) and fn.value.id == "router"):
            continue
        if not dec.args:
            continue
        arg = dec.args[0]
        if isinstance(arg, ast.Constant) and arg.value == path:
            return True
    return False


def ensure_route(src: str, name: str, method: str, path: str) -> tuple[str, bool]:
    node = _function(src, name)
    if _route_matches(node, method, path):
        return src, False
    lines = src.splitlines(True)
    insert_at = (min(d.lineno for d in node.decorator_list) - 1) if node.decorator_list else (node.lineno - 1)
    indent = re.match(r"\s*", lines[node.lineno - 1]).group(0)
    lines.insert(insert_at, f'{indent}@router.{method}("{path}")\n')
    out = "".join(lines)
    ast.parse(out)
    if not _route_matches(_function(out, name), method, path):
        raise RuntimeError(f"failed to attach route {method.upper()} {path} to {name}")
    return out, True


def patch_future_rev2(path: Path) -> bool:
    if not path.exists():
        return False
    s = path.read_text(encoding="utf-8")
    old = '''    first = start - 1\n    while first > 0 and lines[first - 1].lstrip().startswith("@"):\n        first -= 1\n    lines[first:end] = [replacement.rstrip() + "\\n\\n"]\n'''
    new = '''    # 0.9.4.8a: replace only the function node. Route decorators must survive.\n    lines[start - 1:end] = [replacement.rstrip() + "\\n\\n"]\n'''
    if old in s:
        s = s.replace(old, new, 1)
        ast.parse(s)
        path.write_text(s, encoding="utf-8")
        return True
    return False


def patch_main_version(path: Path) -> bool:
    s = path.read_text(encoding="utf-8")
    if 'version="0.9.4.8a"' in s:
        return False
    if 'version="0.9.4.8"' not in s:
        raise RuntimeError("expected FastAPI version 0.9.4.8")
    s = s.replace('version="0.9.4.8"', 'version="0.9.4.8a"', 1)
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    args = ap.parse_args()
    root = Path(args.root)
    mp_path = root / "app/routers/marketplaces.py"
    main_path = root / "app/main.py"
    rev2_path = root / "tools/apply_rev2_0_9_4_8.py"

    mp = mp_path.read_text(encoding="utf-8")
    if "0.9.4.8 REV2 SEQUENTIAL CONFIRMED PRINT" not in mp:
        raise RuntimeError("expected 0.9.4.8 REV2 foundation")

    changed = []
    for name, method, route in ROUTES:
        mp, did = ensure_route(mp, name, method, route)
        if did:
            changed.append(f"{method.upper()} {route}")

    if MARKER not in mp:
        anchor = "# 0.9.4.8 REV2 SEQUENTIAL CONFIRMED PRINT"
        if anchor in mp:
            mp = mp.replace(anchor, anchor + f"\n# {MARKER}", 1)
        else:
            mp = f"# {MARKER}\n" + mp
    ast.parse(mp)
    mp_path.write_text(mp, encoding="utf-8")

    version_changed = patch_main_version(main_path)
    future_fixed = patch_future_rev2(rev2_path)

    # Final route audit against the file we are about to publish.
    final = mp_path.read_text(encoding="utf-8")
    missing = []
    for name, method, route in ROUTES:
        if not _route_matches(_function(final, name), method, route):
            missing.append(f"{method.upper()} {route}")
    if missing:
        raise RuntimeError("missing routes after patch: " + ", ".join(missing))

    print("OK: 0.9.4.8a critical routes restored")
    print("restored:", ", ".join(changed) if changed else "already present")
    print("version_changed:", version_changed)
    print("future_rev2_decorator_bug_fixed:", future_fixed)


if __name__ == "__main__":
    main()
