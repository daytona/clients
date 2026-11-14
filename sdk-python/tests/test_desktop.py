# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import Any, get_args

import httpx
import pytest
from _repo import _repo_file

from daytona.v2 import codes
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport, SyncTransport
from daytona.v2.computer_use import AsyncComputerUseService
from daytona.v2.computer_use_types import (
    ComputerUseActivityStatus,
    ComputerUseDesktopFailureCode,
    ComputerUseDesktopState,
)

_STATUS_CASES = (
    ({"status": "inactive", "desktop": "not_started"}, "inactive", "not_started", None),
    ({"status": "inactive", "desktop": "starting"}, "inactive", "starting", None),
    ({"status": "active", "desktop": "ready"}, "active", "ready", None),
    (
        {
            "status": "inactive",
            "desktop": "failed",
            "cause": {
                "code": "desktop_exited",
                "message": "desktop session ended unexpectedly",
            },
        },
        "inactive",
        "failed",
        ("desktop_exited", "desktop session ended unexpectedly"),
    ),
)

_INVALID_STATUS_CASES = (
    ({"desktop": "ready"}, "missing status"),
    ({"status": "active"}, "missing desktop"),
    ({"status": 123, "desktop": "ready"}, "non-string status"),
    ({"status": "active", "desktop": True}, "non-string desktop"),
    (
        {"status": "inactive", "desktop": "rebooting", "cause": "crashed"},
        "unrecognized pair with non-dict cause",
    ),
    (
        {"status": "inactive", "desktop": "rebooting", "cause": {"code": 123, "message": "x"}},
        "unrecognized pair with non-string cause code",
    ),
    (
        {
            "status": "active",
            "desktop": "ready",
            "cause": {"code": "desktop_exited", "message": "msg"},
        },
        "cause on ready",
    ),
    (
        {
            "status": "inactive",
            "desktop": "starting",
            "cause": {"code": "desktop_exited", "message": "msg"},
        },
        "cause on starting",
    ),
    (
        {
            "status": "inactive",
            "desktop": "not_started",
            "cause": {"code": "desktop_exited", "message": "msg"},
        },
        "cause on not_started",
    ),
    ({"status": "inactive", "desktop": "failed"}, "failed without cause"),
    ({"status": "inactive", "desktop": "failed", "cause": None}, "failed with null cause"),
    ({"status": "inactive", "desktop": "failed", "cause": "crashed"}, "failed with non-dict cause"),
    (
        {"status": "inactive", "desktop": "failed", "cause": {"message": "failed"}},
        "failed with missing cause code",
    ),
    (
        {"status": "inactive", "desktop": "failed", "cause": {"code": 123, "message": "failed"}},
        "failed with non-string cause code",
    ),
    (
        {"status": "inactive", "desktop": "failed", "cause": {"code": "addon_crashed"}},
        "failed with missing cause message",
    ),
    (
        {"status": "inactive", "desktop": "failed", "cause": {"code": "addon_crashed", "message": 456}},
        "failed with non-string cause message",
    ),
)

_UNKNOWN_STATUS_CASES = (
    ({"status": "degraded", "desktop": "ready"}, "degraded", "ready", "unknown status"),
    ({"status": "inactive", "desktop": "rebooting"}, "inactive", "rebooting", "unknown desktop"),
    ({"status": "active", "desktop": "starting"}, "active", "starting", "active starting"),
    ({"status": "active", "desktop": "not_started"}, "active", "not_started", "active not_started"),
    ({"status": "inactive", "desktop": "ready"}, "inactive", "ready", "inactive ready"),
    (
        {"status": "inactive", "desktop": "rebooting", "cause": None},
        "inactive",
        "rebooting",
        "unrecognized pair with null cause",
    ),
)

_INVALID_RESTART_CASES = (
    ({"status": "active", "desktop": "ready"}, "missing message"),
    ({"message": 123, "status": "active", "desktop": "ready"}, "non-string message"),
    ({"message": "restarted", "status": "inactive", "desktop": "ready"}, "inactive status"),
    ({"message": "restarted", "status": "active", "desktop": "starting"}, "non-ready desktop"),
    ({"message": "restarted", "desktop": "ready"}, "missing status"),
    ({"message": "restarted", "status": "active"}, "missing desktop"),
    ({"message": "restarted", "status": 123, "desktop": "ready"}, "non-string status"),
    ({"message": "restarted", "status": "active", "desktop": True}, "non-string desktop"),
)


class _Stub:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> dict[str, Any]:
        self.specs.append(spec)
        return self.response


@pytest.mark.parametrize(("payload", "activity_status", "desktop", "cause"), _STATUS_CASES)
@pytest.mark.anyio
async def test_status_parses_desktop_and_cause(
    payload: dict[str, Any],
    activity_status: str,
    desktop: str,
    cause: tuple[str, str] | None,
) -> None:
    transport = _Stub(payload)

    status = await AsyncComputerUseService(transport).status()  # type: ignore[arg-type]

    assert [(spec.method, spec.path) for spec in transport.specs] == [
        ("GET", "/computeruse/status"),
    ]
    assert status.status == activity_status
    assert status.desktop == desktop
    if cause is None:
        assert status.cause is None
    else:
        assert status.cause is not None
        assert status.cause.code == cause[0]
        assert status.cause.message == cause[1]


@pytest.mark.anyio
async def test_status_normalizes_unknown_failure_code_and_keeps_raw_value() -> None:
    payload = {
        "status": "inactive",
        "desktop": "failed",
        "cause": {
            "code": "future_desktop_failure",
            "message": "a future desktop implementation failed",
        },
    }

    status = await AsyncComputerUseService(_Stub(payload)).status()  # type: ignore[arg-type]

    assert status.desktop == "failed"
    assert status.cause is not None
    assert status.cause.code == "unknown"
    assert status.cause.raw_code == "future_desktop_failure"
    assert status.cause.message == "a future desktop implementation failed"


@pytest.mark.anyio
async def test_restart_posts_exact_path_and_parses_response() -> None:
    transport = _Stub(
        {
            "message": "computer use restarted",
            "status": "active",
            "desktop": "ready",
        }
    )

    result = await AsyncComputerUseService(transport).restart()  # type: ignore[arg-type]

    assert [(spec.method, spec.path) for spec in transport.specs] == [
        ("POST", "/computeruse/restart"),
    ]
    assert result.message == "computer use restarted"
    assert result.status == "active"
    assert result.desktop == "ready"
    assert transport.specs[0].json_body is None


@pytest.mark.anyio
async def test_start_and_restart_send_a_resolution_only_when_given() -> None:
    from daytona.v2 import ComputerUseResolution

    lifecycle = {"message": "computer use started", "status": {}}
    restarted = {"message": "computer use restarted", "status": "active", "desktop": "ready"}
    full_hd = ComputerUseResolution(width=1920, height=1080)

    read_back = {"status": "active", "desktop": "ready", "resolution": {"width": 1920, "height": 1080}}

    start = _Script(lifecycle, read_back)
    await AsyncComputerUseService(_Script(lifecycle)).start()  # type: ignore[arg-type]
    await AsyncComputerUseService(start).start(resolution=full_hd)  # type: ignore[arg-type]
    assert [(spec.path, spec.json_body) for spec in start.specs][:1] == [
        ("/computeruse/start", {"resolution": {"width": 1920, "height": 1080}}),
    ]

    restart = _Script(restarted, read_back)
    await AsyncComputerUseService(restart).restart(resolution=full_hd)  # type: ignore[arg-type]
    assert [(spec.path, spec.json_body) for spec in restart.specs][:1] == [
        ("/computeruse/restart", {"resolution": {"width": 1920, "height": 1080}}),
    ]


class _Script:
    """Answers each request with the next scripted response."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> dict[str, Any]:
        self.specs.append(spec)
        return self.responses.pop(0)


_STARTED = {"message": "computer use started", "status": {}}
_RESTARTED = {"message": "computer use restarted", "status": "active", "desktop": "ready"}


@pytest.mark.anyio
async def test_start_reads_back_the_resolution_it_sent() -> None:
    from daytona.v2 import ComputerUseResolution

    full_hd = ComputerUseResolution(width=1920, height=1080)

    # A plain start reads nothing back.
    plain = _Script(_STARTED)
    await AsyncComputerUseService(plain).start()  # type: ignore[arg-type]
    assert [spec.path for spec in plain.specs] == ["/computeruse/start"]

    # The status reports the requested resolution.
    reported = _Script(
        _STARTED, {"status": "active", "desktop": "ready", "resolution": {"width": 1920, "height": 1080}}
    )
    result = await AsyncComputerUseService(reported).start(resolution=full_hd)  # type: ignore[arg-type]
    assert result.message == "computer use started"
    assert [(spec.method, spec.path) for spec in reported.specs] == [
        ("POST", "/computeruse/start"),
        ("GET", "/computeruse/status"),
    ]

    # A guest that predates the status field: the first display decides.
    older = _Script(
        _STARTED,
        {"status": "active", "desktop": "ready"},
        {"displays": [{"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "isActive": True}]},
    )
    await AsyncComputerUseService(older).start(resolution=full_hd)  # type: ignore[arg-type]
    assert [(spec.method, spec.path) for spec in older.specs] == [
        ("POST", "/computeruse/start"),
        ("GET", "/computeruse/status"),
        ("GET", "/computeruse/display/info"),
    ]


_UNAPPLIED = "not the requested 1920x1080; a guest that predates resolution selection ignores it"


@pytest.mark.parametrize(
    ("reads", "message"),
    [
        pytest.param(
            [{"status": "active", "desktop": "ready", "resolution": {"width": 1280, "height": 720}}],
            f"desktop runs at 1280x720, {_UNAPPLIED}",
            id="the status reports another resolution",
        ),
        pytest.param(
            [
                {"status": "active", "desktop": "ready"},
                {"displays": [{"id": 0, "x": 0, "y": 0, "width": 1280, "height": 720, "isActive": True}]},
            ],
            f"desktop runs at 1280x720, {_UNAPPLIED}",
            id="an older guest runs its default",
        ),
        pytest.param(
            [{"status": "active", "desktop": "ready", "resolution": None}, {"displays": []}],
            f"desktop runs at an unknown resolution, {_UNAPPLIED}",
            id="an older guest reports no display",
        ),
    ],
)
@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.anyio
async def test_an_unapplied_resolution_raises_unsupported_operation(
    reads: list[dict[str, Any]], message: str, verb: str
) -> None:
    from daytona.v2 import ComputerUseResolution
    from daytona.v2.errors import UnsupportedOperationError

    transport = _Script(_STARTED if verb == "start" else _RESTARTED, *reads)
    service = AsyncComputerUseService(transport)  # type: ignore[arg-type]
    with pytest.raises(UnsupportedOperationError) as exc_info:
        await getattr(service, verb)(resolution=ComputerUseResolution(width=1920, height=1080))
    assert exc_info.value.message == message
    assert (exc_info.value.status_code, exc_info.value.code, exc_info.value.source) == (
        400,
        "UNSUPPORTED_OPERATION",
        "SDK",
    )


def test_the_status_resolution_is_typed_and_kept_out_of_extra_fields() -> None:
    from daytona.v2 import ComputerUseResolution
    from daytona.v2.computer_use_core import parse_computer_use_status

    hd = {"width": 1280, "height": 720}
    typed = ComputerUseResolution(width=1280, height=720)
    not_started = parse_computer_use_status(
        {"status": "inactive", "desktop": "not_started", "resolution": hd, "future": 1}
    )
    assert not_started.resolution == typed
    assert not_started.extra_fields == {"future": 1}
    assert (
        parse_computer_use_status({"status": "active", "desktop": "ready", "resolution": hd}).resolution
        == typed
    )
    failed = parse_computer_use_status(
        {
            "status": "inactive",
            "desktop": "failed",
            "cause": {"code": "desktop_exited", "message": "m"},
            "resolution": hd,
        }
    )
    assert failed.resolution == typed
    unknown = parse_computer_use_status({"status": "paused", "desktop": "parked", "resolution": hd})
    assert (unknown.desktop, unknown.resolution) == ("unknown", typed)
    # None is "not reported", as an absent field is.
    assert (
        parse_computer_use_status({"status": "active", "desktop": "ready", "resolution": None}).resolution
        is None
    )
    assert parse_computer_use_status({"status": "active", "desktop": "ready"}).resolution is None


def _desktop_not_ready_response(request: httpx.Request) -> httpx.Response:
    assert request.method == "POST"
    assert request.url.path == "/computeruse/mouse/click"
    return httpx.Response(
        409,
        json={
            "message": "desktop session failed; call POST /computeruse/restart",
            "source": "DAYTONA_DAEMON",
            "code": "DESKTOP_NOT_READY",
        },
    )


def test_sync_desktop_not_ready_maps_to_conflict_subclass() -> None:
    from daytona.v2.errors import ConflictError, DesktopNotReadyError

    transport = SyncTransport("http://toolbox.invalid")
    transport._client = httpx.Client(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(_desktop_not_ready_response),
        base_url="http://toolbox.invalid",
    )
    try:
        with pytest.raises(DesktopNotReadyError) as raised:
            transport.send(
                RequestSpec(
                    "POST",
                    "/computeruse/mouse/click",
                    json_body={"x": 10, "y": 20, "button": "left"},
                )
            )
    finally:
        transport.close()

    assert isinstance(raised.value, ConflictError)
    assert raised.value.status_code == 409
    assert raised.value.code == "DESKTOP_NOT_READY"
    assert raised.value.source == "DAYTONA_DAEMON"
    assert raised.value.message == ("desktop session failed; call POST /computeruse/restart")


@pytest.mark.anyio
async def test_async_desktop_not_ready_maps_to_conflict_subclass() -> None:
    from daytona.v2.errors import ConflictError, DesktopNotReadyError

    async def click() -> None:
        transport = AsyncTransport("http://toolbox.invalid")
        transport._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            transport=httpx.MockTransport(_desktop_not_ready_response),
            base_url="http://toolbox.invalid",
        )
        try:
            await transport.send(
                RequestSpec(
                    "POST",
                    "/computeruse/mouse/click",
                    json_body={"x": 10, "y": 20, "button": "left"},
                )
            )
        finally:
            await transport.close()

    with pytest.raises(DesktopNotReadyError) as raised:
        await click()

    assert isinstance(raised.value, ConflictError)
    assert raised.value.status_code == 409
    assert raised.value.code == "DESKTOP_NOT_READY"
    assert raised.value.source == "DAYTONA_DAEMON"
    assert raised.value.message == ("desktop session failed; call POST /computeruse/restart")


@pytest.mark.parametrize(("payload", "description"), _INVALID_STATUS_CASES)
def test_status_parser_rejects_invalid_wire_invariants(
    payload: dict[str, Any],
    description: str,
) -> None:
    from daytona.v2.computer_use_core import parse_computer_use_status
    from daytona.v2.errors import BadRequestError, DaytonaError

    with pytest.raises(BadRequestError) as exc_info:
        parse_computer_use_status(payload)

    assert isinstance(exc_info.value, DaytonaError)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "BAD_REQUEST"
    assert exc_info.value.source == "SDK"
    assert (
        "Invalid computer-use status response" in exc_info.value.message
        or "Invalid computer-use desktop failure" in exc_info.value.message
    ), description


@pytest.mark.parametrize(("payload", "raw_status", "raw_desktop", "description"), _UNKNOWN_STATUS_CASES)
def test_status_parser_reads_an_unrecognized_pair_as_unknown_with_raw_values_kept(
    payload: dict[str, Any],
    raw_status: str,
    raw_desktop: str,
    description: str,
) -> None:
    from daytona.v2 import ComputerUseUnknownStatus
    from daytona.v2.computer_use_core import parse_computer_use_status

    status = parse_computer_use_status(payload)

    assert isinstance(status, ComputerUseUnknownStatus), description
    assert status.status == "unknown"
    assert status.desktop == "unknown"
    assert status.raw_status == raw_status
    assert status.raw_desktop == raw_desktop
    assert status.cause is None


def test_status_parser_keeps_a_failure_attached_to_an_unrecognized_pair() -> None:
    from daytona.v2 import ComputerUseUnknownStatus
    from daytona.v2.computer_use_core import parse_computer_use_status

    status = parse_computer_use_status(
        {
            "status": "active",
            "desktop": "failed",
            "cause": {"code": "future_display_failure", "message": "display backend changed"},
            "futureField": 7,
        }
    )

    assert isinstance(status, ComputerUseUnknownStatus)
    assert status.raw_status == "active"
    assert status.raw_desktop == "failed"
    assert status.cause is not None
    assert status.cause.code == "unknown"
    assert status.cause.raw_code == "future_display_failure"
    assert status.cause.message == "display backend changed"
    assert status.extra_fields == {"futureField": 7}


@pytest.mark.parametrize(("payload", "description"), _INVALID_RESTART_CASES)
def test_restart_parser_rejects_invalid_wire_invariants(
    payload: dict[str, Any],
    description: str,
) -> None:
    from daytona.v2.computer_use_core import parse_computer_use_restart
    from daytona.v2.errors import BadRequestError, DaytonaError

    with pytest.raises(BadRequestError) as exc_info:
        parse_computer_use_restart(payload)

    assert isinstance(exc_info.value, DaytonaError)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "BAD_REQUEST"
    assert exc_info.value.source == "SDK"
    assert "Invalid computer-use restart response" in exc_info.value.message, description


@pytest.mark.anyio
async def test_status_returns_slotted_variant_types_and_preserves_extra_fields() -> None:
    from daytona.v2 import (
        ComputerUseFailedStatus,
        ComputerUseNotStartedStatus,
        ComputerUseReadyStatus,
        ComputerUseStartingStatus,
    )

    not_started = await AsyncComputerUseService(
        _Stub({"status": "inactive", "desktop": "not_started", "custom": 1})
    ).status()  # type: ignore[arg-type]
    assert isinstance(not_started, ComputerUseNotStartedStatus)
    assert not_started.status == "inactive"
    assert not_started.desktop == "not_started"
    assert not_started.cause is None
    assert not_started.extra_fields == {"custom": 1}

    starting = await AsyncComputerUseService(
        _Stub({"status": "inactive", "desktop": "starting", "custom": 2})
    ).status()  # type: ignore[arg-type]
    assert isinstance(starting, ComputerUseStartingStatus)
    assert starting.status == "inactive"
    assert starting.desktop == "starting"
    assert starting.cause is None
    assert starting.extra_fields == {"custom": 2}

    ready = await AsyncComputerUseService(
        _Stub({"status": "active", "desktop": "ready", "custom": 3})
    ).status()  # type: ignore[arg-type]
    assert isinstance(ready, ComputerUseReadyStatus)
    assert ready.status == "active"
    assert ready.desktop == "ready"
    assert ready.cause is None
    assert ready.extra_fields == {"custom": 3}

    failed = await AsyncComputerUseService(
        _Stub(
            {
                "status": "inactive",
                "desktop": "failed",
                "cause": {"code": "future_error", "message": "boom", "future_field": "abc"},
                "future_field": 4,
            }
        )
    ).status()  # type: ignore[arg-type]
    assert isinstance(failed, ComputerUseFailedStatus)
    assert failed.status == "inactive"
    assert failed.desktop == "failed"
    assert failed.cause is not None
    assert failed.cause.code == "unknown"
    assert failed.cause.raw_code == "future_error"
    assert failed.cause.message == "boom"
    assert failed.cause.extra_fields == {"future_field": "abc"}
    assert failed.extra_fields == {"future_field": 4}


@pytest.mark.anyio
async def test_restart_preserves_extra_fields() -> None:
    result = await AsyncComputerUseService(
        _Stub(
            {
                "message": "Desktop restarted",
                "status": "active",
                "desktop": "ready",
                "extra": "value",
            }
        )
    ).restart()  # type: ignore[arg-type]
    assert result.message == "Desktop restarted"
    assert result.status == "active"
    assert result.desktop == "ready"
    assert result.extra_fields == {"extra": "value"}


def test_desktop_lifetime_types_and_error_are_exported() -> None:
    import daytona.v2 as daytona
    from daytona.v2 import (
        ComputerUseDesktopFailure,
        ComputerUseFailedStatus,
        ComputerUseNotStartedStatus,
        ComputerUseReadyStatus,
        ComputerUseRestartResponse,
        ComputerUseStartingStatus,
        ComputerUseStatusResponse,
        ComputerUseUnknownStatus,
        DesktopNotReadyError,
    )
    from daytona.v2.errors import ConflictError

    assert daytona.ComputerUseStatusResponse is ComputerUseStatusResponse
    assert daytona.ComputerUseNotStartedStatus is ComputerUseNotStartedStatus
    assert daytona.ComputerUseStartingStatus is ComputerUseStartingStatus
    assert daytona.ComputerUseReadyStatus is ComputerUseReadyStatus
    assert daytona.ComputerUseFailedStatus is ComputerUseFailedStatus
    assert daytona.ComputerUseUnknownStatus is ComputerUseUnknownStatus
    assert daytona.ComputerUseActivityStatus is ComputerUseActivityStatus
    assert daytona.ComputerUseDesktopState is ComputerUseDesktopState
    assert daytona.ComputerUseDesktopFailure is ComputerUseDesktopFailure
    assert daytona.ComputerUseRestartResponse is ComputerUseRestartResponse
    assert daytona.DesktopNotReadyError is DesktopNotReadyError
    assert issubclass(DesktopNotReadyError, ConflictError)
    assert "DESKTOP_NOT_READY" in codes.DAEMON_ERROR_CODES


def test_desktop_vocabulary_matches_contract() -> None:
    # Desktop vocabulary must match the committed contract.
    contract = _repo_file("contract/toolbox-openapi.json")
    schemas = json.loads(contract.read_text())["components"]["schemas"]
    activity_statuses = schemas["ComputerUseActivityStatus"]["enum"]
    states = schemas["ComputerUseDesktopState"]["enum"]
    failure_codes = schemas["ComputerUseDesktopFailureCode"]["enum"]

    assert set(get_args(ComputerUseActivityStatus)) == set(activity_statuses) | {"unknown"}
    assert set(activity_statuses) == {"active", "inactive"}
    assert set(get_args(ComputerUseDesktopState)) == set(states) | {"unknown"}
    assert set(states) == {"not_started", "starting", "ready", "failed"}
    assert set(failure_codes) == {
        value for value in get_args(ComputerUseDesktopFailureCode) if value != "unknown"
    }
    assert set(failure_codes) == {
        "desktop_exited",
        "addon_crashed",
        "start_failed",
        "start_interrupted",
        "session_ended",
        "session_replaced",
        "state_corrupt",
        "state_persistence_failed",
    }


# --- the synchronous twin, through the real generated class -------------------


def test_sync_computer_use_status_and_restart_go_through_the_bridge() -> None:
    """The generated ComputerUseService must submit on the bridge loop and
    hand back the same parsed objects the async service produces."""
    from daytona.v2 import ComputerUseService
    from daytona.v2._bridge import SyncBridge

    status_payload = {"status": "inactive", "desktop": "starting"}
    restart_payload = {"message": "computer use restarted", "status": "active", "desktop": "ready"}
    stub = _Stub(status_payload)
    bridge = SyncBridge()
    try:
        service = ComputerUseService(AsyncComputerUseService(stub), bridge)  # type: ignore[arg-type]

        status = service.status()
        assert status.status == "inactive"
        assert status.desktop == "starting"

        stub.response = restart_payload
        result = service.restart()
        assert result.message == "computer use restarted"
        assert result.desktop == "ready"

        assert [(spec.method, spec.path) for spec in stub.specs] == [
            ("GET", "/computeruse/status"),
            ("POST", "/computeruse/restart"),
        ]
    finally:
        bridge.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "inactive", "desktop": "not_started", "cause": None},
        {"status": "inactive", "desktop": "starting", "cause": None},
        {"status": "active", "desktop": "ready", "cause": None},
    ],
)
def test_status_parser_reads_an_explicit_null_cause_outside_the_failed_state_as_absent(
    payload: dict[str, Any],
) -> None:
    from daytona.v2.computer_use_core import parse_computer_use_status

    status = parse_computer_use_status(payload)

    assert (status.status, status.desktop, status.cause) == (payload["status"], payload["desktop"], None)


# Shared with the TypeScript SDK's unit.test.ts: each malformed status or
# restart response names its field, with the same message in both SDKs.
_STATUS_FIELD_CASES = [
    (None, "the body must be an object"),
    ([], "the body must be an object"),
    ("inactive", "the body must be an object"),
    ({"desktop": "ready"}, "status must be a string"),
    ({"status": 123, "desktop": "ready"}, "status must be a string"),
    ({"status": "active"}, "desktop must be a string"),
    ({"status": "active", "desktop": True}, "desktop must be a string"),
    ({"status": "inactive", "desktop": "rebooting", "cause": "crashed"}, "cause must be null or an object"),
    ({"status": "inactive", "desktop": "failed", "cause": []}, "cause must be null or an object"),
    ({"status": "inactive", "desktop": "failed", "cause": {"message": "m"}}, "cause.code must be a string"),
    (
        {"status": "inactive", "desktop": "rebooting", "cause": {"code": 123, "message": "x"}},
        "cause.code must be a string",
    ),
    (
        {"status": "inactive", "desktop": "failed", "cause": {"code": "addon_crashed"}},
        "cause.message must be a string",
    ),
    (
        {"status": "inactive", "desktop": "failed", "cause": {"code": "c", "message": 456}},
        "cause.message must be a string",
    ),
    (
        {"status": "inactive", "desktop": "failed"},
        "cause is required when status is 'inactive' and desktop is 'failed'",
    ),
    (
        {"status": "inactive", "desktop": "failed", "cause": None},
        "cause is required when status is 'inactive' and desktop is 'failed'",
    ),
    (
        {"status": "active", "desktop": "ready", "cause": {"code": "desktop_exited", "message": "m"}},
        "cause must be absent when status is 'active' and desktop is 'ready'",
    ),
    (
        {"status": "inactive", "desktop": "starting", "cause": {"code": "desktop_exited", "message": "m"}},
        "cause must be absent when status is 'inactive' and desktop is 'starting'",
    ),
]
_STATUS_FIELD_CASES += [
    (
        {"status": "active", "desktop": "ready", "resolution": "1280x720"},
        "resolution must be null or an object",
    ),
    (
        {"status": "active", "desktop": "ready", "resolution": [1280, 720]},
        "resolution must be null or an object",
    ),
    (
        {"status": "active", "desktop": "ready", "resolution": {"height": 720}},
        "resolution.width must be a non-negative int32",
    ),
    (
        {"status": "active", "desktop": "ready", "resolution": {"width": 1280.5, "height": 720}},
        "resolution.width must be a non-negative int32",
    ),
    (
        {"status": "active", "desktop": "ready", "resolution": {"width": True, "height": 720}},
        "resolution.width must be a non-negative int32",
    ),
    (
        {"status": "active", "desktop": "ready", "resolution": {"width": 1280, "height": -1}},
        "resolution.height must be a non-negative int32",
    ),
    (
        {"status": "active", "desktop": "ready", "resolution": {"width": 2**31, "height": 720}},
        "resolution.width must be a non-negative int32",
    ),
    (
        {"status": "active", "desktop": "ready", "resolution": {"width": 1280, "height": "720"}},
        "resolution.height must be a non-negative int32",
    ),
]
_RESTART_FIELD_CASES = [
    (None, "the body must be an object"),
    ([], "the body must be an object"),
    ({"status": "active", "desktop": "ready"}, "message must be a string"),
    ({"message": 123, "status": "active", "desktop": "ready"}, "message must be a string"),
    ({"message": "m", "desktop": "ready"}, "status must be 'active'"),
    ({"message": "m", "status": "inactive", "desktop": "ready"}, "status must be 'active'"),
    ({"message": "m", "status": 123, "desktop": "ready"}, "status must be 'active'"),
    ({"message": "m", "status": "active"}, "desktop must be 'ready'"),
    ({"message": "m", "status": "active", "desktop": "starting"}, "desktop must be 'ready'"),
    ({"message": "m", "status": "active", "desktop": True}, "desktop must be 'ready'"),
]


@pytest.mark.parametrize(("payload", "detail"), _STATUS_FIELD_CASES)
def test_a_malformed_status_response_names_its_field(payload: object, detail: str) -> None:
    from daytona.v2.computer_use_core import parse_computer_use_status
    from daytona.v2.errors import BadRequestError

    with pytest.raises(BadRequestError) as exc_info:
        parse_computer_use_status(payload)
    assert exc_info.value.message == f"Invalid computer-use status response: {detail}"
    assert (exc_info.value.status_code, exc_info.value.code, exc_info.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


@pytest.mark.parametrize(("payload", "detail"), _RESTART_FIELD_CASES)
def test_a_malformed_restart_response_names_its_field(payload: object, detail: str) -> None:
    from daytona.v2.computer_use_core import parse_computer_use_restart
    from daytona.v2.errors import BadRequestError

    with pytest.raises(BadRequestError) as exc_info:
        parse_computer_use_restart(payload)
    assert exc_info.value.message == f"Invalid computer-use restart response: {detail}"
