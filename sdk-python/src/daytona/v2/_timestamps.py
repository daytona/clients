# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""RFC 3339 timestamps read from the wire into timezone-aware datetimes."""

from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import Optional

from .errors import create_error

# One RFC 3339 rule for every timestamp the SDK reads or checks (toolbox
# responses, search bounds, a create response's token expiry), the same as the
# TypeScript SDK's ``isRfc3339``: ASCII digits only, month 01-12, the day within
# its month, hour 00-23, minute and second 00-59 (no leap second: neither a
# ``datetime`` nor a ``Date`` can express one), date and time joined by ``T``
# (or ``t``), an optional fraction of any length, and ``Z`` or an offset within
# +-23:59; years 0001-9999.
_RFC3339 = re.compile(
    r"(?P<date>(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12][0-9]|3[01]))[Tt]"
    r"(?P<time>(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9])"
    r"(?:\.(?P<fraction>[0-9]+))?(?P<offset>[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def rfc3339_timestamp(value: object) -> bool:
    """Whether ``value`` is an RFC 3339 timestamp by the rule above."""
    if not isinstance(value, str):
        return False
    match = _RFC3339.fullmatch(value)
    if match is None:
        return False
    year, month, day = int(match.group("year")), int(match.group("month")), int(match.group("day"))
    return year >= 1 and day <= calendar.monthrange(year, month)[1]


def parse_timestamp(value: str, field: str) -> datetime:
    """A timezone-aware ``datetime`` for an RFC 3339 wire timestamp.

    Anything else is a malformed response: ``BadRequestError`` naming ``field``
    (the same error the generated-model boundary raises).
    """
    match = _RFC3339.fullmatch(value) if rfc3339_timestamp(value) else None
    if match is None:
        raise create_error(f"expected {field}: not an RFC 3339 timestamp", 400, "BAD_REQUEST", "SDK")
    fraction = (match.group("fraction") or "")[:6].ljust(6, "0")
    offset = match.group("offset")
    offset = "+00:00" if offset in ("Z", "z") else offset
    try:
        return datetime.fromisoformat(f"{match.group('date')}T{match.group('time')}.{fraction}{offset}")
    except ValueError as exc:
        raise create_error(f"expected {field}: not an RFC 3339 timestamp", 400, "BAD_REQUEST", "SDK") from exc


def parse_optional_timestamp(value: Optional[str], field: str) -> Optional[datetime]:
    return None if value is None else parse_timestamp(value, field)
