#!/usr/bin/env python3
"""Static release smoke-check for the cumulative FulfillCore 0.7.9.0 overlay."""
from __future__ import annotations

import compileall
import hashlib
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "app"

def require(path: pathlib.Path, needle: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    if needle not in text:
        raise AssertionError(f"{path.relative_to(ROOT)}: missing {needle!r}")


def main() -> int:
    if not compileall.compile_dir(APP, quiet=1, force=True):
        raise AssertionError("Python compilation failed")
    require(APP / "main.py", 'version="0.7.9.0"')
    require(APP / "routers" / "marketplaces.py", "/wb/packing/release-order")
    require(APP / "routers" / "marketplaces.py", "acquire_packing_lease")
    require(APP / "services" / "runtime_reliability.py", "fc_packing_leases")
    require(APP / "services" / "runtime_reliability.py", "device_id VARCHAR(64)")
    require(APP / "services" / "client_location_guard.py", "SYSTEM_LOCATION_CODES")
    require(APP / "routers" / "layout.py", "LOCATION_QR_LABEL_BATCH")
    require(APP / "static" / "app.js", "scheduleWbScanner")
    require(APP / "static" / "app.js", "releaseActiveWbPackingLease")
    require(APP / "static" / "app.js", "FULFILLCORE_DEVICE_ID")

    agent = APP / "downloads" / "FulfillCore_Print_Agent_1.2.0_RELIABLE_MULTI_DEVICE.zip"
    if not zipfile.is_zipfile(agent):
        raise AssertionError("Print Agent archive is not a valid ZIP")
    with zipfile.ZipFile(agent) as zf:
        names = set(zf.namelist())
        for required in {"print_agent.ps1", "setup.ps1", "README_1.2.0_RU.txt"}:
            if required not in names:
                raise AssertionError(f"Print Agent archive missing {required}")
        ps = zf.read("print_agent.ps1").decode("utf-8-sig")
        if 'Write-JobState $jobId "submitted" "spool-accepted"' not in ps:
            raise AssertionError("Print Agent durable submitted state missing")
        if "ACK failed after successful print" not in ps:
            raise AssertionError("Print Agent ACK/no-reprint guard missing")
        if '"X-Print-Agent-Station" = $station' not in ps:
            raise AssertionError("Print Agent station-bound header missing")
    digest = hashlib.sha256(agent.read_bytes()).hexdigest()
    print(f"OK: FulfillCore 0.7.9.0 overlay smoke-check passed; Print Agent SHA-256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise
