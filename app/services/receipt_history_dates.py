"""Read-only receipt history ordering. No database or inventory operations.

ISO text and datetime instances get comparable keys without changing values
returned to the UI or written to the database. Legacy timezone-naive values
are treated as UTC *for ordering only*. Empty/invalid dates stay visible last.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

_INVALID = (0, 0)
_MICROSECONDS_PER_SECOND = 1_000_000
_MICROSECONDS_PER_DAY = 86_400 * _MICROSECONDS_PER_SECOND


def history_date_key(value: Any) -> tuple[int, int]:
    """Return (valid, UTC ordinal microseconds), never mixed str/datetime.

    Integer arithmetic preserves microseconds and also supports datetime.min
    and datetime.max with UTC offsets, without a platform timestamp() call.
    This function is a sorting projection; it does not rewrite the source date.
    """
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, time.min)
        elif isinstance(value, str):
            raw = value.strip()
            if not raw or len(raw) > 128:
                return _INVALID
            # Python 3.10 does not accept the Z suffix in fromisoformat().
            if raw.endswith(('Z', 'z')):
                raw = raw[:-1] + '+00:00'
            parsed = datetime.fromisoformat(raw)
        else:
            return _INVALID

        offset = parsed.utcoffset() or timedelta(0)
        local_us = (
            parsed.toordinal() * _MICROSECONDS_PER_DAY
            + (parsed.hour * 3600 + parsed.minute * 60 + parsed.second)
            * _MICROSECONDS_PER_SECOND
            + parsed.microsecond
        )
        offset_us = (
            offset.days * _MICROSECONDS_PER_DAY
            + offset.seconds * _MICROSECONDS_PER_SECOND
            + offset.microseconds
        )
        return (1, local_us - offset_us)
    except (TypeError, ValueError, OverflowError):
        return _INVALID
