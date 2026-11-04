# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Config provenance and user-owned URL fields at migration boundaries."""

from __future__ import annotations

import ast
import json
from difflib import ndiff
from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize(
    "body",
    [
        body.replace("CLIENT", client)
        for client in ("Daytona", "AsyncDaytona")
        for body in (
            "from helper import cfg\nclient = CLIENT(cfg)\n",
            "from helper import factory\nclient = CLIENT(factory())\n",
            "def build(cfg: DaytonaConfig):\n    return CLIENT(cfg)\n",
            "cfg = DaytonaConfig()\ncustomize(cfg)\nclient = CLIENT(cfg)\n",
            "cfg = DaytonaConfig()\ncustomize(config=cfg)\nclient = CLIENT(config=cfg)\n",
            "cfg = DaytonaConfig()\nsetattr(cfg, 'server_url', url)\nclient = CLIENT(cfg)\n",
            "cfg = DaytonaConfig()\nalias = cfg\ncustomize(alias)\nclient = CLIENT(cfg)\n",
            "cfg = DaytonaConfig()\nvalues = [cfg]\ncustomize(values)\nclient = CLIENT(cfg)\n",
            "cfg = DaytonaConfig()\ncfg.update()\nclient = CLIENT(cfg)\n",
            "cfg = DaytonaConfig()\nclient = CLIENT(cfg)\nsetattr(cfg, 'api_url', url)\n",
            "cfg = DaytonaConfig()\nclient = CLIENT(cfg)\nfield = 'api_url'\nsetattr(cfg, field, url)\n",
            "cfg = DaytonaConfig()\nclient = CLIENT(cfg)\nalias = cfg\nalias.api_url = url\n",
        )
    ],
)
@pytest.mark.parametrize("drop", [False, True])
def test_constructor_refuses_when_config_origin_is_unverified(
    tmp_path: Path, body: str, drop: bool
) -> None:
    # Given configurations whose custom URL cannot be ruled out locally.
    source = (
        "from daytona import Daytona, AsyncDaytona, DaytonaConfig\nfrom helper import customize\n"
        + body
    )
    files = {
        "app.py": source,
        "helper.py": "from settings import cfg\ndef factory():\n    return cfg\ndef customize(cfg):\n    cfg.api_url = 'https://self.example/api'\n",
        "settings.py": "from daytona import DaytonaConfig\ncfg = DaytonaConfig()\n",
        "requirements.txt": "daytona==0.224.0\n",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    # When the CLI checks every proven client construction.
    status = cli.main(
        [str(tmp_path), "--force", "--yes", *(["--drop-api-url"] if drop else [])]
    )
    # Then refusal is atomic; an assertion keeps config code user-owned.
    assert status == (0 if drop else 2)
    output = (tmp_path / "app.py").read_text()
    if drop:
        assert ast.dump(ast.parse(output)) == ast.dump(ast.parse(source))
        differences = tuple(ndiff(source.splitlines(), output.splitlines()))
        assert not any(line.startswith("- ") for line in differences)
        assert any(line.startswith("+ ") for line in differences)
        assert all(
            line[2:].lstrip()
            in {
                "# FIXME(daytona-upgrade): SDK 2.x refuses URL fields in the client config; this config is shared or its ownership is unresolved. Give the client a URL-free config and keep this URL write for other readers.",
                "# FIXME(daytona-upgrade): SDK 2.x refuses URL fields in the client config; this config is shared or its ownership is unresolved. Give the client a URL-free config.",
            }
            for line in differences
            if line.startswith("+ ")
        )
    else:
        assert output == source
    assert (tmp_path / "requirements.txt").read_text() == (
        "daytona[v0-sdk]>=2,<3\n" if drop else files["requirements.txt"]
    )


@pytest.mark.parametrize(
    "argument", ["cfg", "config=cfg", "DaytonaConfig()", "", "None", "config=None"]
)
def test_constructor_migrates_when_config_is_locally_resolved(argument: str) -> None:
    # Given a direct local construction, an inline config, or defaults.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        + f"Daytona({argument}).create()\n"
    )
    # When the boundary can establish the config's provenance.
    result = migrate(source)
    # Then the call migrates normally without a manual URL item.
    assert result.source == source.replace(".create()", ".v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        "class Wrapper:\n    def __init__(self):\n        self.api_url = None\n",
        "class Wrapper:\n    def __init__(self):\n        self.api_url = 'https://my-backend'\n",
        "setattr(settings, 'api_url', None)\n",
        "settings.api_url, key = values\n",
        "cfg = object()\ncfg.api_url = None\n",
    ],
)
def test_url_fields_stay_untouched_when_target_is_not_a_config(body: str) -> None:
    # Given unrelated URL fields in a module which also imports Daytona.
    source = "from daytona import Daytona\n" + body
    # When URL writes are inspected.
    result = migrate(source)
    # Then neither spelling nor value invites a rewrite or diagnostic.
    assert result.source == source
    assert result.items == ()


def test_report_marks_manual_config_when_user_asserts_cloud(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a config supplied by a caller with no locally provable origin.
    source = "from daytona import Daytona\ndef build(cfg):\n    return Daytona(cfg)\n"
    (tmp_path / "app.py").write_text(source)
    # When asserting Cloud usage via the real JSON CLI surface.
    status = cli.main([str(tmp_path), "--json", "--drop-api-url"])
    report = json.loads(capsys.readouterr().out)
    # Then uncertainty is reported, and the constructor's code is untouched apart from guidance.
    assert status == 0
    assert [item["kind"] for item in report["items"]] == ["config-field"]
    output = (tmp_path / "app.py").read_text()
    assert ast.dump(ast.parse(output)) == ast.dump(ast.parse(source))
    assert (
        "# FIXME(daytona-upgrade): SDK 2.x refuses URL fields in the client config"
        in output
    )


def test_config_call_stays_unchanged_when_asserted_origin_is_unresolved() -> None:
    # Given a config which escapes before construction, despite a default keyword.
    source = "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig(api_url=None)\ncustomize(cfg)\nDaytona(cfg)\n"
    # When the operator asserts Cloud usage for this unresolved origin.
    result = migrate(source, drop=True)
    # Then the entire call stays user-owned, with explicit shared-URL guidance.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert "FIXME(daytona-upgrade)" in result.source
    assert [item["kind"] for item in result.items] == ["config-field", "config-field"]


@pytest.mark.parametrize(
    "source",
    [
        "import daytona as sdk\ncfg = sdk.DaytonaConfig()\nsdk.AsyncDaytona(cfg).create()\n",
        "from daytona import Daytona, DaytonaConfig\ndef run():\n    cfg: DaytonaConfig = DaytonaConfig()\n    Daytona(cfg).create()\n",
    ],
)
def test_local_config_proves_when_constructor_is_qualified_or_in_function(
    source: str,
) -> None:
    # Given a local config with a qualified or function-local construction.
    # When applying receiver-proven migration.
    result = migrate(source)
    # Then local provenance permits the call-site rewrite.
    assert result.source == source.replace(".create()", ".v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("annotation", ["DaytonaConfig", "'DaytonaConfig'"])
def test_url_write_is_checked_when_parameter_is_annotated(annotation: str) -> None:
    # Given a config parameter explicitly typed in its owning scope.
    source = f"from daytona import DaytonaConfig\ndef run(cfg: {annotation}):\n    cfg.api_url = 'https://self.example'\n"
    # When preflighting writes rather than resolving a constructor argument.
    result = migrate(source)
    # Then type provenance is sufficient to refuse the custom write.
    assert [item["kind"] for item in result.items] == ["custom-api-url"]


def test_outer_config_default_write_is_removed_when_lexical_origin_is_proven() -> None:
    # Given an enclosing real config with a removable default URL write.
    source = "from daytona import DaytonaConfig\ncfg = DaytonaConfig()\ndef run():\n    cfg.api_url = None\n"
    # When checking URL writes using lexical provenance.
    result = migrate(source)
    # Then the real config's default write is removed like a constructor keyword.
    assert result.source == source.replace("cfg.api_url = None", "pass")
    assert result.items == ()
