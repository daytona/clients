# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The v2 error surface: names, constructors, defaults and the codes catalog.

tests/errors-contract.test.ts in the TypeScript SDK asserts the same facts.
"""

from __future__ import annotations

import inspect
from types import MappingProxyType
from typing import Callable

import pytest

import daytona
from daytona import v2
from daytona.v2 import codes
from daytona.v2._core import parse_error_payload


def test_category_names_match_the_typescript_sdk() -> None:
    assert issubclass(v2.AuthenticationError, daytona.DaytonaAuthenticationError)
    assert issubclass(v2.ForbiddenError, daytona.DaytonaForbiddenError)
    assert issubclass(v2.ClientTimeoutError, daytona.DaytonaTimeoutError)
    assert issubclass(v2.RequestTimeoutError, daytona.DaytonaTimeoutError)
    assert not issubclass(v2.ClientTimeoutError, v2.RequestTimeoutError)
    assert issubclass(v2.GitAuthFailedError, v2.AuthenticationError)
    assert parse_error_payload(401, {}).__class__ is v2.AuthenticationError
    assert parse_error_payload(403, {}).__class__ is v2.ForbiddenError


def test_errors_carry_only_code_not_a_second_spelling() -> None:
    error = v2.NotFoundError("missing", status_code=404, code="NOT_FOUND", source="DAYTONA_API")
    assert error.code == "NOT_FOUND"
    assert not hasattr(error, "error_code")


def test_constructors_take_the_message_and_keyword_only_options() -> None:
    error = v2.ConflictError(
        "busy", status_code=409, code="CONFLICT", source="DAYTONA_API", trace_id="t-1", retry_after_seconds=3
    )
    assert (error.message, error.status_code, error.code, error.source) == (
        "busy",
        409,
        "CONFLICT",
        "DAYTONA_API",
    )
    assert (error.trace_id, error.retry_after_seconds) == ("t-1", 3)
    with pytest.raises(TypeError):
        v2.ConflictError("busy", 409)  # type: ignore[misc]
    params = inspect.signature(v2.DaytonaError).parameters
    assert [name for name, p in params.items() if p.kind is not inspect.Parameter.KEYWORD_ONLY] == ["message"]


def test_bare_errors_carry_no_metadata() -> None:
    error = v2.BadRequestError("bad")
    assert (error.status_code, error.code, error.source, error.trace_id) == (None, None, None, None)


def test_transfer_cancelled_error_defaults_match_the_typescript_sdk() -> None:
    error = v2.TransferCancelledError()
    assert (str(error), error.status_code, error.code, error.source) == (
        "file transfer cancelled",
        499,
        "REQUEST_CANCELLED",
        "SDK",
    )
    custom = v2.TransferCancelledError("upload cancelled", source="CALLER")
    assert (custom.message, custom.status_code, custom.code, custom.source) == (
        "upload cancelled",
        499,
        "REQUEST_CANCELLED",
        "CALLER",
    )


def test_limit_exceeded_error_defaults_its_code() -> None:
    assert v2.LimitExceededError("limit reached").code == "LIMIT_EXCEEDED"
    assert v2.LimitExceededError("limit reached", limit="max_concurrent_sandboxes", current=3, max=3).max == 3


def test_image_build_failed_error_keeps_build_error_separate_from_message() -> None:
    bare = v2.ImageBuildFailedError("image img_1 build failed", image_id="img_1")
    assert bare.message == "image img_1 build failed"
    assert bare.build_error is None
    recorded = v2.ImageBuildFailedError(
        "image img_1 build failed", image_id="img_1", build_error="RUN exited 1"
    )
    assert recorded.build_error == "RUN exited 1"
    assert recorded.message == "image img_1 build failed"
    from_wire = parse_error_payload(
        409, {"code": "IMAGE_BUILD_FAILED", "source": "DAYTONA_API", "message": "failed"}
    )
    assert isinstance(from_wire, v2.ImageBuildFailedError)
    assert from_wire.build_error is None


def test_unknown_codes_are_kept_verbatim_with_the_status_category() -> None:
    error = parse_error_payload(404, {"code": "SOMETHING_NEW", "source": "DAYTONA_SOMETHING", "message": "x"})
    assert type(error) is v2.NotFoundError
    assert (error.code, error.source) == ("SOMETHING_NEW", "DAYTONA_SOMETHING")
    assert not codes.is_known_error_code("SOMETHING_NEW")


def test_codes_namespace_has_the_typescript_member_names() -> None:
    assert sorted(codes.__all__) == [
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
    assert {name for name in dir(codes) if not name.startswith("_")} == set(codes.__all__)


def test_codes_catalogs_are_read_only() -> None:
    for name in (
        "API_ERROR_CODES",
        "DAEMON_ERROR_CODES",
        "GENERIC_ERROR_CODES",
        "PROXY_ERROR_CODES",
        "ROUTER_ERROR_CODES",
    ):
        assert isinstance(getattr(codes, name), frozenset)
    for name in (
        "CODE_STATUS",
        "API_CODE_EXTRAS",
        "DAEMON_CODE_EXTRAS",
        "PROXY_CODE_EXTRAS",
        "ROUTER_CODE_EXTRAS",
    ):
        mapping = getattr(codes, name)
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping["X"] = 1  # type: ignore[index]
    assert codes.CODE_STATUS["NOT_FOUND"] == 404
    assert codes.is_known_error_code("PROCESS_NOT_FOUND")
    assert not codes.is_known_error_code(None)


def test_build_log_interrupted_error_takes_the_shared_options() -> None:
    error = v2.BuildLogInterruptedError("lost", cursor="c-1", trace_id="t-1", status_code=503)
    assert (error.cursor, error.code, error.source, error.trace_id, error.status_code) == (
        "c-1",
        "BUILD_LOG_INTERRUPTED",
        "SDK",
        "t-1",
        503,
    )


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (
            lambda: v2.ClientTimeoutError("x", status_code=None, code=None, source=None),
            (408, "REQUEST_TIMEOUT", "SDK"),
        ),
        (lambda: v2.ConnectionError("x", source=None), (None, None, "SDK")),
        (
            lambda: v2.TransferCancelledError("x", status_code=None, code=None, source=None),
            (499, "REQUEST_CANCELLED", "SDK"),
        ),
        (lambda: v2.LimitExceededError("x", code=None), (None, "LIMIT_EXCEEDED", None)),
        (
            lambda: v2.BuildLogInterruptedError("x", code=None, source=None),
            (None, "BUILD_LOG_INTERRUPTED", "SDK"),
        ),
    ],
)
def test_an_explicit_none_takes_the_default_like_typescript_undefined(
    make: Callable[[], v2.DaytonaError], expected: tuple[object, ...]
) -> None:
    error = make()
    assert (error.status_code, error.code, error.source) == expected
