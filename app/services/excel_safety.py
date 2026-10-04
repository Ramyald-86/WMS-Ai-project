from __future__ import annotations

import io
import json
import re
import uuid
from datetime import datetime, time, timezone
from decimal import Decimal

_ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_MAX_CELL_TEXT = 32767


def safe_excel_value(value):
    """Normalize values that openpyxl/Excel can reject.

    PostgreSQL TIMESTAMPTZ is returned as a timezone-aware datetime. Excel does
    not support timezone information, so keep the instant in UTC and strip the
    tzinfo before writing. Also remove C0 control characters and normalize
    common SQL/Python value types.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, time):
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            value = bytes(value).decode("utf-8", "replace")
        except Exception:
            value = str(value)
    if isinstance(value, (dict, list, tuple, set)):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    if isinstance(value, str):
        value = _ILLEGAL_XLSX.sub("", value)
        if len(value) > _MAX_CELL_TEXT:
            value = value[:_MAX_CELL_TEXT]
        return value
    return value



def append_safe(worksheet, values):
    """Append one row after normalizing every cell before openpyxl sees it."""
    normalized = [safe_excel_value(value) for value in values]
    worksheet.append(normalized)
    row_no = worksheet.max_row
    for col_no, value in enumerate(normalized, 1):
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            worksheet.cell(row_no, col_no).data_type = "s"
    return row_no

def sanitize_workbook(workbook):
    """Sanitize every existing cell before saving a workbook."""
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                value = safe_excel_value(cell.value)
                if value != cell.value:
                    cell.value = value
                # Values from operators/clients are data, not formulas.
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                    cell.data_type = "s"
    return workbook


def workbook_bytes(workbook) -> bytes:
    sanitize_workbook(workbook)
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()
