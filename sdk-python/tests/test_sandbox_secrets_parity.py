# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Runtime sandbox-secrets parity.

Mirrored by ``sdk-typescript/tests/sandboxSecretsParity.test.ts``: the two
suites pin the same decode, error-mapping, and replace-wire behaviour.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Literal, Optional

import pytest
from _handles import lazy_sandbox
from _repo import _repo_file
from network_policy_test_server import FakeApi, JsonValue, PlannedResponse, json_body
from test_core import _config_at
from typing_extensions import assert_never

from daytona.v2 import (
    BadRequestError,
    ConflictError,
    DaytonaError,
    NotFoundError,
    PreconditionFailedError,
    PreconditionRequiredError,
    SandboxSecretsInfo,
)
from daytona.v2.daytona import AsyncDaytona

pytestmark = pytest.mark.anyio

JsonField = JsonValue
SecretsCall = Literal["get", "replace"]


async def _call(client: AsyncDaytona, call: SecretsCall) -> SandboxSecretsInfo:
    match call:
        case "get":
            return await lazy_sandbox(client, "sbx").get_secrets()
        case "replace":
            return await lazy_sandbox(client, "sbx").replace_secrets(secrets=["TOKEN"], if_match='"sv-3"')
        case unreachable:
            assert_never(unreachable)


def _secrets_body(**overrides: JsonField) -> dict[str, JsonField]:
    body: dict[str, JsonField] = {
        "secrets": ["TOKEN"],
        "desired_revision": 3,
        "applied_revision": None,
        "status": None,
        "error": None,
        "capture_status": None,
        "capture_unresolved_count": None,
    }
    body.update(overrides)
    return body


async def _get(body: dict[str, JsonField]) -> SandboxSecretsInfo:
    with FakeApi([PlannedResponse(200, body, headers={"ETag": '"sv-3"'})]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            return await lazy_sandbox(client, "sbx").get_secrets()
        finally:
            await client.close()


@pytest.mark.parametrize(
    ("status", "applied_revision", "error"),
    [
        (None, None, None),
        ("pending", 2, None),
        ("applied", 3, None),
        ("failed", 2, "secret update failed"),
    ],
)
async def test_get_decodes_every_runtime_status(
    status: Optional[str], applied_revision: Optional[int], error: Optional[str]
) -> None:
    info = await _get(_secrets_body(status=status, applied_revision=applied_revision, error=error))

    assert (info.status, info.applied_revision, info.error) == (status, applied_revision, error)
    assert info.desired_revision == 3


@pytest.mark.parametrize(
    ("capture_status", "capture_unresolved_count"),
    [
        (None, None),
        ("complete", 0),
        ("partial", 2),
        ("future-status", None),
    ],
)
async def test_get_decodes_every_capture_status(
    capture_status: Optional[str], capture_unresolved_count: Optional[int]
) -> None:
    info = await _get(
        _secrets_body(capture_status=capture_status, capture_unresolved_count=capture_unresolved_count)
    )

    assert (info.capture_status, info.capture_unresolved_count) == (capture_status, capture_unresolved_count)


async def test_get_decodes_absent_optional_fields_as_none() -> None:
    info = await _get({"secrets": [], "desired_revision": 0})

    assert info == SandboxSecretsInfo(secrets=(), desired_revision=0, etag='"sv-3"', http_status=200)


async def test_every_contract_response_property_is_decoded() -> None:
    contract = _repo_file("contract/api-openapi.json")
    schema = json.loads(contract.read_text())["components"]["schemas"]["SandboxSecrets"]
    full_body = _secrets_body(
        applied_revision=2,
        status="pending",
        error="x",
        capture_status="partial",
        capture_unresolved_count=1,
    )
    assert set(full_body) == set(schema["properties"])

    info = await _get(full_body)

    decoded = {name: getattr(info, name) for name in schema["properties"]}
    assert all(value is not None for value in decoded.values()), decoded
    # Same drift check as the TS suite: the SDK type carries exactly the
    # contract's properties plus its own response metadata, no more.
    sdk_fields = {field.name for field in dataclasses.fields(info)} - {"etag", "http_status"}
    assert sdk_fields == set(schema["properties"])


def _envelope(code: str, message: str, trace_id: str) -> dict[str, JsonField]:
    return {"code": code, "message": message, "source": "DAYTONA_API", "trace_id": trace_id}


async def test_get_maps_a_missing_sandbox_to_not_found_without_retrying() -> None:
    with FakeApi([PlannedResponse(404, _envelope("NOT_FOUND", "sandbox not found", "trace-404"))]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(NotFoundError) as exc:
                await lazy_sandbox(client, "sbx").get_secrets()
        finally:
            await client.close()

    error = exc.value
    assert (error.status_code, error.code, error.source, error.trace_id) == (
        404,
        "NOT_FOUND",
        "DAYTONA_API",
        "trace-404",
    )
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    ("status", "code", "secrets", "expected"),
    [
        (412, "STALE_SECRETS_REVISION", ["TOKEN"], PreconditionFailedError),
        (409, "SECRETS_TRANSITION_IN_PROGRESS", ["TOKEN"], ConflictError),
        (409, "CONFLICT", ["TOKEN"], ConflictError),
        (400, "BAD_REQUEST", ["TOKEN", "TOKEN"], BadRequestError),
        (404, "NOT_FOUND", ["TOKEN"], NotFoundError),
        (428, "IF_MATCH_REQUIRED", ["TOKEN"], PreconditionRequiredError),
    ],
)
async def test_replace_maps_api_errors_without_retrying(
    status: int, code: str, secrets: list[str], expected: type[DaytonaError]
) -> None:
    body = _envelope(code, f"{code.lower()} from the api", f"trace-{status}")
    with FakeApi([PlannedResponse(status, body)]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(expected) as exc:
                await lazy_sandbox(client, "sbx").replace_secrets(secrets=secrets, if_match='"sv-3"')
        finally:
            await client.close()

    error = exc.value
    assert (error.status_code, error.code, error.source, error.trace_id) == (
        status,
        code,
        "DAYTONA_API",
        f"trace-{status}",
    )
    assert len(api.requests) == 1
    # Duplicates are the server's call: the SDK forwards the list verbatim.
    assert json_body(api.requests[0]) == {"secrets": secrets}


async def test_replace_refuses_missing_secrets_locally_instead_of_sending_an_empty_body() -> None:
    with FakeApi([]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(BadRequestError) as exc:
                await lazy_sandbox(client, "sbx").replace_secrets(secrets=None, if_match='"sv-3"')  # type: ignore[arg-type]
        finally:
            await client.close()

    assert (exc.value.status_code, exc.value.code, exc.value.source) == (400, "BAD_REQUEST", "SDK")
    assert api.requests == []


@pytest.mark.parametrize(("call", "status"), [("get", 200), ("replace", 202)])
async def test_a_response_without_etag_is_an_sdk_error_not_a_tag_rebuilt_from_the_revision(
    call: SecretsCall, status: int
) -> None:
    with FakeApi([PlannedResponse(status, _secrets_body())]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(BadRequestError) as exc:
                await _call(client, call)
        finally:
            await client.close()

    error = exc.value
    assert (error.status_code, error.code, error.source, error.message) == (
        400,
        "BAD_REQUEST",
        "SDK",
        "sandbox secrets response is missing the ETag header",
    )
    assert len(api.requests) == 1


async def test_detach_all_sends_an_explicit_empty_array_with_if_match() -> None:
    accepted = _secrets_body(secrets=[], desired_revision=4, applied_revision=3, status="pending")
    with FakeApi([PlannedResponse(202, accepted, headers={"ETag": '"sv-4"'})]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            info = await lazy_sandbox(client, "sbx").replace_secrets(secrets=[], if_match='"sv-3"')
        finally:
            await client.close()

    request = api.requests[0]
    assert (request.method, request.path, request.headers["If-Match"]) == (
        "PUT",
        "/sandboxes/sbx/secrets",
        '"sv-3"',
    )
    assert json_body(request) == {"secrets": []}
    assert (info.secrets, info.desired_revision, info.etag, info.http_status) == ((), 4, '"sv-4"', 202)
