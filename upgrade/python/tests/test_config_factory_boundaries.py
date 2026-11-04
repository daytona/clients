# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Factory argument expansion must not bypass the constructor URL policy."""

from __future__ import annotations

import pytest
from test_url_write_transactions import migrate


@pytest.mark.parametrize(
    "factory",
    [
        ("model_copy", "update"),
        ("copy", "update"),
        ("model_validate", "obj"),
        ("parse_obj", "obj"),
    ],
)
@pytest.mark.parametrize("receiver", ["cfg", "DaytonaConfig", "DaytonaConfig()"])
@pytest.mark.parametrize("spread", ["**{'PAYLOAD': VALUE}", "**{**{'PAYLOAD': VALUE}}"])
def test_known_custom_url_refuses_when_factory_kwargs_are_literal(
    factory: tuple[str, str], receiver: str, spread: str
) -> None:
    # Given literal kwargs hiding a known custom URL inside the factory's payload.
    member, payload = factory
    argument = spread.replace("PAYLOAD", payload).replace(
        "VALUE", "{'api_url': 'https://custom.example/api'}"
    )
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"Daytona({receiver}.{member}({argument})).create()\n"
    )
    # When a Cloud assertion permits only unresolved URLs, not known custom URLs.
    result = migrate(source, drop=True)
    # Then argument expansion cannot turn custom evidence into an overridable warning.
    assert any(item["kind"] == "custom-api-url" for item in result.items)


@pytest.mark.parametrize(
    "call", ["cfg.model_copy(update=PAYLOAD)", "DaytonaConfig.model_validate(PAYLOAD)"]
)
def test_cloud_payload_resolves_when_earlier_literal_url_is_shadowed(call: str) -> None:
    # Given a literal mapping whose effective URL is Cloud, like a constructor's.
    payload = "{'api_url': 'https://custom.example/api', 'api_url': 'https://app.daytona.io/api'}"
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"Daytona({call.replace('PAYLOAD', payload)}).create()\n"
    )
    # When inspecting the effective mapping value rather than a dead entry.
    result = migrate(source)
    # Then no phantom custom URL remains after the Cloud URL is dropped.
    assert not any(item["kind"] == "custom-api-url" for item in result.items)
    assert "api_url" not in result.source


@pytest.mark.parametrize("member", ["model_json_schema", "model_construct"])
@pytest.mark.parametrize("receiver", ["cfg", "DaytonaConfig"])
def test_dataclass_guidance_when_using_additional_pydantic_members(
    member: str, receiver: str
) -> None:
    # Given remaining Pydantic v2 equivalents of schema/construct on a proven config.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"print({receiver}.{member}())\nDaytona(cfg).create()\n"
    )
    # When migrating the config read.
    result = migrate(source)
    # Then removed model APIs do not masquerade as URL escapes.
    assert any(item["kind"] == "config-field" for item in result.items)
    assert not any(item["kind"] == "custom-api-url" for item in result.items)


@pytest.mark.parametrize(
    "member",
    [
        "model_copy",
        "copy",
        "model_validate",
        "parse_obj",
        "model_validate_json",
        "parse_raw",
    ],
)
def test_unknown_payload_refuses_when_factory_receiver_is_inline(member: str) -> None:
    # Given an inline constructor and factory sharing the same start position.
    argument = "update=options" if member in {"model_copy", "copy"} else "options"
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"Daytona(DaytonaConfig().{member}({argument})).create()\n"
    )
    # When checking the outer factory rather than accidentally the inner constructor.
    result = migrate(source)
    # Then unknown factory results require the Cloud assertion.
    assert any(item["kind"] == "custom-api-url" for item in result.items)


@pytest.mark.parametrize(
    "call",
    [
        "cfg.model_copy(**{'update': {'api_url': 'https://custom.example/api'}, 'update': {'api_key': 'k'}})",
        "DaytonaConfig.model_construct(**{'api_url': 'https://custom.example/api', 'api_url': 'https://app.daytona.io/api'})",
    ],
)
def test_factory_kwargs_resolve_when_custom_url_is_overwritten(call: str) -> None:
    # Given kwargs whose effective mapping no longer contains a custom URL.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        + f"Daytona({call}).create()\n"
    )
    # When inspecting the effective Python dictionary rather than dead entries.
    result = migrate(source)
    # Then argument expansion does not create a phantom refusal.
    assert not any(item["kind"] == "custom-api-url" for item in result.items)
