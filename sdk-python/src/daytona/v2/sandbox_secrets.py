# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Final, Sequence

import httpx

from ._core import finite_number, parse_json_response
from .errors import create_error
from .types import JsonObject, JsonValue, SandboxSecretsInfo

_INPUT_FIELD: Final[str] = "secrets"


def serialize_sandbox_secrets_input(secrets: Sequence[str]) -> JsonObject:
    if not isinstance(secrets, Sequence) or isinstance(secrets, (str, bytes, bytearray)):
        raise create_error("sandbox secrets must be an array of strings", 400, "BAD_REQUEST", "SDK")
    items: list[str] = []
    for item in secrets:
        if not isinstance(item, str):
            raise create_error("sandbox secrets must be an array of strings", 400, "BAD_REQUEST", "SDK")
        items.append(item)
    return {_INPUT_FIELD: items}


def parse_sandbox_secrets_response(response: httpx.Response) -> SandboxSecretsInfo:
    payload = _expect_response_object(parse_json_response(response.content, what="sandbox secrets response"))
    etag = response.headers.get("etag")
    if not isinstance(etag, str) or not etag:
        raise create_error("sandbox secrets response is missing the ETag header", 400, "BAD_REQUEST", "SDK")
    return SandboxSecretsInfo(
        secrets=tuple(_required_string_list(payload.get("secrets"), field_name="secrets")),
        desired_revision=_required_int(payload.get("desired_revision"), field_name="desired_revision"),
        etag=etag,
        http_status=response.status_code,
        applied_revision=_optional_int(payload.get("applied_revision"), field_name="applied_revision"),
        status=_optional_str(payload.get("status")),
        error=_optional_str(payload.get("error")),
        capture_status=_optional_str(payload.get("capture_status")),
        capture_unresolved_count=_optional_int(
            payload.get("capture_unresolved_count"),
            field_name="capture_unresolved_count",
        ),
    )


def _expect_response_object(value: JsonValue) -> JsonObject:
    if isinstance(value, dict):
        return value
    raise create_error("expected a JSON object response", 400, "BAD_REQUEST", "SDK")


def _required_string_list(value: JsonValue, *, field_name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise create_error(f"{field_name} must be an array of strings", 400, "BAD_REQUEST", "SDK")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise create_error(f"{field_name} must be an array of strings", 400, "BAD_REQUEST", "SDK")
        items.append(item)
    return items


def _required_int(value: JsonValue, *, field_name: str) -> int:
    # An integer too large for a double is refused, as in TypeScript, whose
    # JSON.parse reads it as Infinity, which its integer check then refuses.
    if isinstance(value, int) and finite_number(value):
        return value
    raise create_error(f"{field_name} must be an integer", 400, "BAD_REQUEST", "SDK")


def _optional_int(value: JsonValue, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, field_name=field_name)


def _optional_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None
