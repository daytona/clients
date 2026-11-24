# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The SDK's ``ConnectionError`` for a request the HTTP client failed (internal).

The HTTP stack quotes a header it refuses to send in its error text
(``Illegal header value b'Bearer <credential>'``), and httpx chains the
lower layers' errors, which quote it too. A credential must never reach an
error message, a traceback or a telemetry span, so that text is redacted in
the error and in every exception it chains to before anything reads it.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from .errors import ConnectionError

_HEADER_TEXT = re.compile(r"(Illegal header (?:name|value))\b.*", re.DOTALL)


def connection_error(error: httpx.HTTPError) -> ConnectionError:
    """The ``ConnectionError`` for ``error``, with header names and values
    redacted from it and from its causes (in place, so a span or a traceback
    that shows ``error`` shows the redacted text)."""
    redact_header_text(error)
    return ConnectionError(str(error))


def redact_header_text(error: BaseException) -> None:
    """Redact quoted header names and values from ``error`` and every
    exception it chains to (``__cause__`` and ``__context__``)."""
    seen: set[int] = set()
    pending: list[Optional[BaseException]] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if any(isinstance(arg, str) and _HEADER_TEXT.search(arg) for arg in current.args):
            current.args = tuple(
                _HEADER_TEXT.sub(r"\1 (redacted)", arg) if isinstance(arg, str) else arg
                for arg in current.args
            )
        pending.extend((current.__cause__, current.__context__))
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))
