# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Walks over response JSON never recurse without a bound: a body nested past
the decoder's limit is the malformed-response error, and the JSON check is
iterative and ends on a cycle."""

from __future__ import annotations

import gc
import threading
from collections.abc import Iterator

import pytest

from daytona.v2._api_core import expect_volume
from daytona.v2._core import is_json_value, loads_json, parse_json_bytes
from daytona.v2.build_logs import _messages
from daytona.v2.errors import BadRequestError
from daytona.v2.services import _volume
from daytona.v2.types import JsonValue

_DEEP = 100_000


@pytest.fixture(autouse=True)
def _collect() -> Iterator[None]:
    # The deep and cyclic values built here are released now, not by a later
    # garbage-collection pause inside another (timing-sensitive) test.
    yield
    gc.collect()


def _bounded(check: object, seconds: float = 10) -> object:
    """Run ``check()`` in a thread, so a regression that loops fails instead of hanging."""
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(check()), daemon=True)  # type: ignore[operator]
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "the check did not finish"
    return result[0]


@pytest.mark.parametrize("decode", [parse_json_bytes, lambda body: loads_json(body)])
def test_a_body_nested_100000_levels_is_a_contract_error(decode: object) -> None:
    body = ("[" * _DEEP + "]" * _DEEP).encode()
    with pytest.raises(BadRequestError, match="nested too deeply") as raised:
        decode(body)  # type: ignore[operator]
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


def test_invalid_json_still_raises_value_error() -> None:
    with pytest.raises(ValueError):
        loads_json("{")


def test_a_build_log_event_nested_past_the_decoder_limit_is_a_protocol_error() -> None:
    # 63,000 levels: past the decoder's limit, inside the 128 KiB event bound.
    depth = 63_000
    stream = f"event: log\ndata: {'[' * depth}{']' * depth}\n\n".encode()
    with pytest.raises(Exception, match="invalid event JSON") as raised:
        list(_messages([stream]))
    assert not isinstance(raised.value, RecursionError)


def test_is_json_value_refuses_a_cyclic_list_and_dict() -> None:
    cyclic_list: list[object] = []
    cyclic_list.append(cyclic_list)
    cyclic_dict: dict[str, object] = {}
    cyclic_dict["self"] = cyclic_dict
    assert _bounded(lambda: is_json_value(cyclic_list)) is False
    assert _bounded(lambda: is_json_value(cyclic_dict)) is False
    cyclic_list.clear()
    cyclic_dict.clear()


def test_is_json_value_accepts_a_shared_acyclic_value_and_deep_nesting() -> None:
    shared = {"a": [1, 2]}
    assert _bounded(lambda: is_json_value({"x": shared, "y": [shared, shared]})) is True
    deep: object = 1
    for _ in range(200_000):
        deep = [deep]
    assert _bounded(lambda: is_json_value(deep)) is True


def test_a_volume_whose_options_nest_10000_levels_maps() -> None:
    options: JsonValue = {"leaf": True}
    for _ in range(10_000):
        options = {"child": options}
    volume = _volume(
        expect_volume(
            {
                "id": "vol",
                "name": "data",
                "type": "s3",
                "status": "ready",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "options": options,
            }
        )
    )
    assert volume.options is not None and "child" in volume.options


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_loads_json_refuses_the_non_json_constants(constant: str) -> None:
    with pytest.raises(ValueError, match=f"invalid JSON constant {constant}"):
        loads_json(f'{{"x": {constant}}}')


def test_an_overflowing_number_token_still_decodes_and_its_field_decides() -> None:
    # 1e400 is JSON; it decodes to inf, and the field it lands in refuses it.
    value = loads_json('{"x": 1e400}')
    assert isinstance(value, dict) and value["x"] == float("inf")
