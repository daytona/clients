# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""URL fields on config subclasses must not silently redirect credentials to Cloud."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from daytona import AsyncDaytona, Daytona, DaytonaConfig


@pytest.mark.parametrize("client", [Daytona, AsyncDaytona])
@pytest.mark.parametrize("name", ["api_url", "server_url", "toolbox_url"])
@pytest.mark.parametrize("inherited", [False, True])
def test_client_build_is_refused_when_config_subclass_has_url_field(
    client: type[Daytona] | type[AsyncDaytona], name: str, inherited: bool
) -> None:
    # Given a config with a URL on its class, possibly inherited from another subclass.
    class CustomConfig(DaytonaConfig):
        pass

    setattr(CustomConfig, name, "https://self.example/api")

    class InheritedConfig(CustomConfig):
        pass

    config_class = InheritedConfig if inherited else CustomConfig

    # When building either root client (which snapshots the config), then the
    # URL is refused before use.
    with pytest.raises(TypeError, match=f"does not take '{name}'.*stay on SDK 0.x"):
        client(config_class(api_key="k"))


@pytest.mark.parametrize("client", [Daytona, AsyncDaytona])
@pytest.mark.parametrize("name", ["api_url", "server_url", "toolbox_url"])
def test_client_build_is_refused_when_reading_credentials_adds_a_url(
    client: type[Daytona] | type[AsyncDaytona],
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a credential property adds a URL only after the initial URL check.
    class CustomConfig(DaytonaConfig):
        pass

    config = CustomConfig(api_key="k")

    def read_key(self: DaytonaConfig) -> str:
        vars(self)[name] = "https://self.example/api"
        return "k"

    monkeypatch.setattr(CustomConfig, "api_key", property(read_key))
    # The source checkout has no v0 bundle; use its config/client construction seam.
    module = ModuleType("daytona.v0")
    monkeypatch.setattr(module, "_DaytonaConfig", SimpleNamespace, raising=False)
    monkeypatch.setattr(module, "_Daytona", lambda config: config, raising=False)
    monkeypatch.setattr(module, "_AsyncDaytona", lambda config: config, raising=False)
    monkeypatch.setitem(sys.modules, "daytona.v0", module)

    # When / Then: the root refuses the config while it copies the fields, so
    # neither namespace can build a client from it.
    with pytest.raises(TypeError, match=f"does not take '{name}'.*stay on SDK 0.x"):
        client(config)
