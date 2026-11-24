# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client configuration checked before any request: the config and its
``events`` have the right types, boolean options are booleans, and ``get()``'s
toolbox URL override is an http(s) URL. The TypeScript twin is
``tests/config-validation.test.ts``."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import BadRequestError, SandboxEventsConfig
from daytona.v2.types import DaytonaConfig as DaytonaConfigV2
from daytona.v2.types import JsonObject


def _assert_bad_request(error: pytest.ExceptionInfo[BadRequestError], message: str) -> None:
    assert (error.value.status_code, error.value.code, error.value.source) == (400, "BAD_REQUEST", "SDK")
    assert message in str(error.value)


@pytest.mark.parametrize("client", [Daytona, AsyncDaytona])
@pytest.mark.parametrize(
    ("config", "message"),
    [({}, "config must be a DaytonaConfig, got dict"), ("key", "config must be a DaytonaConfig, got str")],
)
def test_a_config_that_is_not_a_daytona_config_is_refused(
    client: Callable[[Any], object], config: object, message: str
) -> None:
    with pytest.raises(BadRequestError) as error:
        client(config)
    _assert_bad_request(error, message)


@pytest.mark.parametrize("client", [Daytona, AsyncDaytona])
@pytest.mark.parametrize(
    "config",
    [{"api_url": "https://example.invalid"}, {"api_key": "k", "toolbox_url": "https://t.invalid"}],
)
def test_a_0x_shaped_config_keeps_the_migration_error_naming_its_url_field(
    client: Callable[[Any], object], config: dict[str, str]
) -> None:
    with pytest.raises(TypeError, match="SDK 2.x targets Daytona Cloud"):
        client(config)


def test_another_daytona_config_class_is_named_in_full() -> None:
    with pytest.raises(BadRequestError) as error:
        Daytona(DaytonaConfigV2(api_key="key"))  # type: ignore[arg-type]
    _assert_bad_request(error, "config must be a DaytonaConfig, got daytona.v2.types.DaytonaConfig")


@pytest.mark.parametrize("events", ["x", {"enabled": False}, 5])
def test_events_that_are_not_a_sandbox_events_config_are_refused(events: object) -> None:
    with pytest.raises(BadRequestError) as error:
        Daytona(DaytonaConfig(api_key="key", events=events))  # type: ignore[arg-type]
    _assert_bad_request(error, f"events must be a SandboxEventsConfig, got {type(events).__name__}")


def test_the_v2_client_refuses_events_that_are_not_a_sandbox_events_config() -> None:
    from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2

    with pytest.raises(BadRequestError) as error:
        AsyncDaytonaV2(DaytonaConfigV2(api_key="key", events="x"))  # type: ignore[arg-type]
    _assert_bad_request(error, "events must be a SandboxEventsConfig, got str")


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (DaytonaConfig(api_key="key", shared_hub="no"), "shared_hub must be a boolean"),  # type: ignore[arg-type]
        (DaytonaConfig(api_key="key", shared_hub=0), "shared_hub must be a boolean"),  # type: ignore[arg-type]
        # Typed bool with no unset value: None would read as false.
        (DaytonaConfig(api_key="key", shared_hub=None), "shared_hub must be a boolean"),  # type: ignore[arg-type]
        (
            DaytonaConfig(api_key="key", events=SandboxEventsConfig(enabled=None)),  # type: ignore[arg-type]
            "events.enabled must be a boolean",
        ),
        (DaytonaConfig(api_key="key", otel_enabled="yes"), "otel_enabled must be a boolean"),  # type: ignore[arg-type]
        (DaytonaConfig(api_key="key", otel_enabled=1), "otel_enabled must be a boolean"),  # type: ignore[arg-type]
        (
            DaytonaConfig(api_key="key", events=SandboxEventsConfig(enabled="no")),  # type: ignore[arg-type]
            "events.enabled must be a boolean",
        ),
    ],
)
def test_a_non_boolean_option_is_refused_when_the_v2_client_is_built(
    config: DaytonaConfig, message: str
) -> None:
    root = Daytona(config)
    with pytest.raises(BadRequestError) as error:
        _ = root.v2
    _assert_bad_request(error, message)


@pytest.mark.parametrize("value", [True, False])
def test_booleans_are_accepted(value: bool) -> None:
    with Daytona(
        DaytonaConfig(
            api_key="key", shared_hub=value, otel_enabled=False, events=SandboxEventsConfig(enabled=value)
        )
    ) as root:
        assert root.v2 is not None


def test_a_non_boolean_otel_enabled_is_refused_before_the_v0_client_is_built() -> None:
    from types import ModuleType

    from daytona._client import _v0_config

    module = ModuleType("fake_v0")
    with pytest.raises(BadRequestError) as error:
        _v0_config(module, DaytonaConfig(api_key="key", otel_enabled="yes"))  # type: ignore[arg-type]
    _assert_bad_request(error, "otel_enabled must be a boolean")


_URL_MESSAGE = "toolbox_url must be an http(s) URL with a host and no query or fragment"
_PLACEHOLDER_MESSAGE = "placeholder in a toolbox URL is only supported in the path"


def _detail(url: str) -> JsonObject:
    return {
        "id": "sbx",
        "class": "linux",
        "name": "sbx",
        "status": "running",
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
        "toolbox_proxy_url": f"{url}/toolbox",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("toolbox_url", "message"),
    [
        ("ftp://toolbox.example/sbx", _URL_MESSAGE),
        ("toolbox.example/sbx", _URL_MESSAGE),
        ("not a url", _URL_MESSAGE),
        ("https://", _URL_MESSAGE),
        ("http:toolbox.example", _URL_MESSAGE),
        ("https://toolbox.example/sbx?x=1", _URL_MESSAGE),
        ("https://toolbox.example/sbx?", _URL_MESSAGE),
        ("https://toolbox.example/sbx#top", _URL_MESSAGE),
        ("https://toolbox.example:99999/sbx", _URL_MESSAGE),
        (" https://toolbox.example/sbx", _URL_MESSAGE),
        ("https://toolbox.example/s bx", _URL_MESSAGE),
        ("https:///toolbox.example/sbx", _URL_MESSAGE),
        ("https://toolbox.example\\sbx", _URL_MESSAGE),
        ("http://%41.toolbox.example/sbx", _URL_MESSAGE),
        ("http://[::1/{sandbox_id}", _URL_MESSAGE),
        ("https://{sandbox_id}.toolbox.example/", _PLACEHOLDER_MESSAGE),
    ],
)
async def test_get_refuses_a_toolbox_url_that_is_no_http_url_before_any_request(
    monkeypatch: pytest.MonkeyPatch, toolbox_url: str, message: str
) -> None:
    with FakeApi([]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(
            DaytonaConfig(api_key="key", events=SandboxEventsConfig(enabled=False))
        ) as root:
            with pytest.raises(BadRequestError) as error:
                await root.v2.get("sbx", toolbox_url=toolbox_url)
        _assert_bad_request(error, message)
        assert fake.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "toolbox_url",
    [
        "https://toolbox.example/sandboxes/sbx",
        "HTTP://127.0.0.1:8080/",
        "http://[::1]:8080/sbx",
        "https://user@toolbox.example/sbx",
        "https://toolbox.example/{sandboxId}/tools",
        "https://toolbox.example/{sandbox_id}",
    ],
)
async def test_get_accepts_an_http_toolbox_url(monkeypatch: pytest.MonkeyPatch, toolbox_url: str) -> None:
    with FakeApi([]) as fake:
        fake._responses.append(PlannedResponse(200, _detail(fake.url)))
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(
            DaytonaConfig(api_key="key", events=SandboxEventsConfig(enabled=False))
        ) as root:
            sandbox = await root.v2.get("sbx", toolbox_url=toolbox_url)
        assert sandbox.id == "sbx"
        assert len(fake.requests) == 1
