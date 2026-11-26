# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""``parse_computer_use_status`` is the public decoder for apps that reach the
toolbox through their own transport: it must give exactly what
``sandbox.computer_use.status()`` gives for the same body (the TypeScript SDK's
``parseComputerUseStatus`` is held to the same)."""

from __future__ import annotations

import dataclasses
import typing

import pytest

import daytona
import daytona.v2
from daytona.v2 import BadRequestError, ComputerUseUnknownStatus, parse_computer_use_status
from daytona.v2._core import RequestSpec
from daytona.v2.computer_use import AsyncComputerUseService

_BODIES: list[tuple[str, object]] = [
    ("not started", {"status": "inactive", "desktop": "not_started"}),
    ("starting, explicit null cause", {"status": "inactive", "desktop": "starting", "cause": None}),
    ("ready, with a future field", {"status": "active", "desktop": "ready", "futureField": {"a": [1]}}),
    (
        "failed",
        {
            "status": "inactive",
            "desktop": "failed",
            "cause": {"code": "addon_crashed", "message": "m", "detail": 1},
        },
    ),
    (
        "failed, unknown code",
        {"status": "inactive", "desktop": "failed", "cause": {"code": "future_code", "message": "m"}},
    ),
    ("an unknown pair", {"status": "paused", "desktop": "dimmed", "cause": {"code": "x", "message": "m"}}),
    ("failed without cause", {"status": "inactive", "desktop": "failed"}),
    (
        "ready with a cause",
        {"status": "active", "desktop": "ready", "cause": {"code": "desktop_exited", "message": "m"}},
    ),
    ("a non-string status", {"status": 1, "desktop": "ready"}),
    ("not an object", ["inactive"]),
    ("None", None),
]


class _Stub:
    def __init__(self, response: object) -> None:
        self.response = response

    async def send(self, spec: RequestSpec) -> object:
        assert (spec.method, spec.path) == ("GET", "/computeruse/status")
        return self.response


def _outcome_of_parse(body: object) -> object:
    try:
        return parse_computer_use_status(body)
    except BadRequestError as exc:
        return ("error", exc.message, exc.status_code, exc.code, exc.source)


async def _outcome_of_status(body: object) -> object:
    try:
        return await AsyncComputerUseService(_Stub(body)).status()  # type: ignore[arg-type]
    except BadRequestError as exc:
        return ("error", exc.message, exc.status_code, exc.code, exc.source)


def test_is_exported_from_v2_only() -> None:
    assert "parse_computer_use_status" in daytona.v2.__all__
    assert "parse_computer_use_status" not in daytona.__all__
    hints = typing.get_type_hints(parse_computer_use_status)
    assert hints["body"] is object


@pytest.mark.parametrize(("name", "body"), _BODIES, ids=[name for name, _ in _BODIES])
@pytest.mark.anyio
async def test_decodes_a_body_as_status_does(name: str, body: object) -> None:
    assert _outcome_of_parse(body) == await _outcome_of_status(body)


def test_returns_the_sdk_model_with_the_raw_values_of_an_unknown_pair() -> None:
    status = parse_computer_use_status({"status": "paused", "desktop": "dimmed", "extra": {"a": [1]}})
    assert status == ComputerUseUnknownStatus(
        raw_status="paused", raw_desktop="dimmed", extra_fields={"extra": {"a": [1]}}
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.raw_status = "x"  # type: ignore[misc]


def test_refuses_a_body_that_breaks_the_contract() -> None:
    with pytest.raises(BadRequestError) as exc_info:
        parse_computer_use_status({"status": "inactive", "desktop": "failed"})
    assert (
        exc_info.value.message,
        exc_info.value.status_code,
        exc_info.value.code,
        exc_info.value.source,
    ) == (
        "Invalid computer-use status response: cause is required when status is 'inactive' and desktop is 'failed'",
        400,
        "BAD_REQUEST",
        "SDK",
    )
