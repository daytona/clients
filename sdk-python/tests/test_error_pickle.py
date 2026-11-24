# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Errors survive ``pickle`` and ``copy`` with their class and every attribute.

Several errors take constructor arguments other than the rendered message
(``SandboxTerminalStateError(sandbox_id, state)``, ...); replaying ``args``
into those constructors failed or, for the create errors, silently put the
whole message into ``idempotency_key``.
"""

from __future__ import annotations

import copy
import pickle
import sys
from typing import Callable

import pytest

import daytona
from daytona.v2 import errors
from daytona.v2._bridge import BridgeError
from daytona.v2.build_logs import BuildLogInterruptedError
from daytona.v2.types import DockerStatus

_DOCKER_STARTING = DockerStatus(owner="daemon", raw_owner="daemon", state="starting", raw_state="starting")
_DOCKER_FAILED = DockerStatus(
    owner="daemon",
    raw_owner="daemon",
    state="failed",
    raw_state="failed",
    reason="daemon exited",
    process_id="proc-1",
)

_CASES: list[Callable[[], errors.DaytonaError]] = [
    lambda: errors.SandboxTerminalStateError("sb-1", "error", raw_state="failed", reason="out of capacity"),
    lambda: errors.SandboxTerminalStateError("sb-1", "destroyed"),
    lambda: errors.SandboxCreateRequestTimeoutError("key-123"),
    lambda: errors.SandboxCreateConnectionError("key-123", "connection refused"),
    lambda: errors.DockerWaitTimeoutError(_DOCKER_STARTING),
    lambda: errors.DockerWaitTimeoutError(None),
    lambda: errors.DockerNotReadyError(_DOCKER_FAILED),
    lambda: errors.ClientTimeoutError(),
    lambda: errors.TransferCancelledError(),
    lambda: errors.ClientClosedError(),
    lambda: errors.ConnectionError("unreachable"),
    lambda: errors.LimitExceededError("limit", status_code=429, limit="max_sandboxes", current=3, max=3),
    lambda: errors.RateLimitError("slow down", status_code=429, code="RATE_LIMITED", retry_after_seconds=7),
    lambda: errors.NotFoundError("missing", status_code=404, code="NOT_FOUND", source="API", trace_id="t-1"),
    lambda: errors.ImageBuildFailedError(
        "build failed", status_code=409, image_id="img-1", build_error="exit 1"
    ),
    lambda: BuildLogInterruptedError("interrupted", cursor="c-9"),
    lambda: BridgeError("misuse"),
]


def _ids() -> list[str]:
    return [type(make()).__name__ for make in _CASES]


def _assert_same(original: errors.DaytonaError, restored: errors.DaytonaError) -> None:
    assert type(restored) is type(original)
    assert restored is not original
    assert str(restored) == str(original)
    assert restored.args == original.args
    assert vars(restored) == vars(original)


@pytest.mark.parametrize("make", _CASES, ids=_ids())
def test_pickle_round_trip_keeps_class_and_attributes(make: Callable[[], errors.DaytonaError]) -> None:
    original = make()
    _assert_same(original, pickle.loads(pickle.dumps(original)))


@pytest.mark.parametrize("make", _CASES, ids=_ids())
@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
def test_copy_keeps_class_and_attributes(
    make: Callable[[], errors.DaytonaError], copier: Callable[[errors.DaytonaError], errors.DaytonaError]
) -> None:
    original = make()
    _assert_same(original, copier(original))


def test_create_errors_keep_the_idempotency_key() -> None:
    timeout = pickle.loads(pickle.dumps(errors.SandboxCreateRequestTimeoutError("key-123")))
    connection = copy.copy(errors.SandboxCreateConnectionError("key-456", "reset"))
    assert timeout.idempotency_key == "key-123"
    assert connection.idempotency_key == "key-456"
    # Still caught by the shared root categories.
    assert isinstance(timeout, daytona.DaytonaTimeoutError)
    assert isinstance(connection, daytona.DaytonaConnectionError)


def test_terminal_state_error_keeps_state_fields() -> None:
    restored = pickle.loads(
        pickle.dumps(
            errors.SandboxTerminalStateError("sb-1", "error", raw_state="failed", reason="no capacity")
        )
    )
    assert (restored.state, restored.raw_state, restored.reason) == ("error", "failed", "no capacity")
    assert restored.status_code == 409 and restored.code == "CONFLICT" and restored.source == "SDK"


def test_docker_errors_keep_status() -> None:
    restored = pickle.loads(pickle.dumps(errors.DockerNotReadyError(_DOCKER_FAILED)))
    assert restored.status == _DOCKER_FAILED
    assert pickle.loads(pickle.dumps(errors.DockerWaitTimeoutError(None))).status is None


@pytest.mark.skipif(sys.version_info < (3, 11), reason="add_note is Python 3.11+")
def test_round_trip_keeps_notes() -> None:
    original = errors.SandboxTerminalStateError("sb-1", "error")
    original.add_note("while waiting for started")
    assert pickle.loads(pickle.dumps(original)).__notes__ == ["while waiting for started"]


class _CallerError(errors.DaytonaError):
    """A caller's subclass with its own constructor shape."""

    def __init__(self, resource: str, attempt: int) -> None:
        super().__init__(f"{resource} failed on attempt {attempt}", source="APP")
        self.resource = resource
        self.attempt = attempt


def test_caller_subclasses_round_trip() -> None:
    restored = pickle.loads(pickle.dumps(_CallerError("db", 3)))
    assert type(restored) is _CallerError
    assert (restored.resource, restored.attempt, restored.source) == ("db", 3, "APP")
