# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The error codes this SDK release knows, per error source (read-only).

``DaytonaError.code`` is an open vocabulary: the server may send a code that
is in none of these catalogs (a newer server), and the error then keeps that
code verbatim and falls back to the class for its HTTP status. Use these
catalogs to recognise a code, not to validate one.

The same members, with the same names, are exported by the TypeScript SDK as
``codes`` from ``@daytona/sdk/v2``.
"""

from __future__ import annotations as _annotations

from types import MappingProxyType as _MappingProxyType
from typing import Mapping as _Mapping
from typing import Optional as _Optional
from typing import Tuple as _Tuple

from . import _codes

__all__ = [
    "API_CODE_EXTRAS",
    "API_ERROR_CODES",
    "CODE_STATUS",
    "DAEMON_CODE_EXTRAS",
    "DAEMON_ERROR_CODES",
    "GENERIC_ERROR_CODES",
    "PROXY_CODE_EXTRAS",
    "PROXY_ERROR_CODES",
    "ROUTER_CODE_EXTRAS",
    "ROUTER_ERROR_CODES",
    "SDK_ERROR_CODES",
    "is_known_error_code",
]

#: Codes the Daytona API (source ``DAYTONA_API``) defines.
API_ERROR_CODES: frozenset[str] = _codes.API_ERROR_CODES
#: Codes the in-sandbox toolbox (source ``DAYTONA_DAEMON``) defines.
DAEMON_ERROR_CODES: frozenset[str] = _codes.DAEMON_ERROR_CODES
#: Codes the toolbox proxy (source ``DAYTONA_PROXY``) defines.
PROXY_ERROR_CODES: frozenset[str] = _codes.PROXY_ERROR_CODES
#: Codes the API router (source ``DAYTONA_ROUTER``) defines.
ROUTER_ERROR_CODES: frozenset[str] = _codes.ROUTER_ERROR_CODES
#: The status-level codes every source may send (``NOT_FOUND``, ``CONFLICT``, ...).
GENERIC_ERROR_CODES: frozenset[str] = _codes.GENERIC_ERROR_CODES

#: Local SDK codes without an HTTP status.
SDK_ERROR_CODES: frozenset[str] = _codes.SDK_ERROR_CODES

#: The HTTP status each known code is sent with.
CODE_STATUS: _Mapping[str, int] = _MappingProxyType(_codes.CODE_STATUS)

#: The optional envelope fields a code may carry beyond ``{code, message,
#: source, trace_id}``, per source; an unlisted code carries none.
API_CODE_EXTRAS: _Mapping[str, _Tuple[str, ...]] = _MappingProxyType(_codes.API_CODE_EXTRAS)
DAEMON_CODE_EXTRAS: _Mapping[str, _Tuple[str, ...]] = _MappingProxyType(_codes.DAEMON_CODE_EXTRAS)
PROXY_CODE_EXTRAS: _Mapping[str, _Tuple[str, ...]] = _MappingProxyType(_codes.PROXY_CODE_EXTRAS)
ROUTER_CODE_EXTRAS: _Mapping[str, _Tuple[str, ...]] = _MappingProxyType(_codes.ROUTER_CODE_EXTRAS)

_KNOWN: frozenset[str] = frozenset(
    _codes.API_ERROR_CODES
    | _codes.DAEMON_ERROR_CODES
    | _codes.PROXY_ERROR_CODES
    | _codes.ROUTER_ERROR_CODES
    | _codes.GENERIC_ERROR_CODES
    | _codes.SDK_ERROR_CODES
)


def is_known_error_code(code: _Optional[str]) -> bool:
    """Whether ``code`` is in one of the catalogs above."""
    return isinstance(code, str) and code in _KNOWN
