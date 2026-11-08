# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""RFC 3339 timestamps read from the wire into timezone-aware datetimes."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .errors import create_error

# ``fromisoformat`` on Python 3.10 accepts neither ``Z`` nor a fraction of
# other than 3 or 6 digits, both of which RFC 3339 allows (Go emits up to 9).
# The ranges match the TypeScript SDK's ``isRfc3339`` exactly: month 01-12,
# hour 00-23, minute and second 00-59 (no leap second), offset hours 00-23 and
# minutes 00-59; the year (0001-9999) and the day for its month are checked by
# ``datetime``, which never normalises an out-of-range value here.
_RFC3339 = re.compile(
    r"(?P<date>\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))[Tt ]"
    r"(?P<time>(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d)"
    r"(?:\.(?P<fraction>\d+))?(?P<offset>[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)


def parse_timestamp(value: str, field: str) -> datetime:
    """A timezone-aware ``datetime`` for an RFC 3339 wire timestamp.

    Anything else is a malformed response: ``BadRequestError`` naming ``field``
    (the same error the generated-model boundary raises).
    """
    match = _RFC3339.fullmatch(value) if isinstance(value, str) else None
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
