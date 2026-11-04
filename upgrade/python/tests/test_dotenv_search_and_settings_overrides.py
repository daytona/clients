# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Search-based loaders and settings overrides cannot silently bypass preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_explicit_dotenv_preflight import CLIENT

from daytona_upgrade.cli import main
from daytona_upgrade.environment_python import scan_python_environment
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import Item, TransformOptions, load_mapping


def inspect_loader(root: Path, loader: str) -> tuple[Item, ...]:
    source = loader + CLIENT
    mapping = load_mapping()
    return scan_python_environment(
        source,
        TransformOptions(mapping),
        SourceContext(
            "src/pkg/app.py",
            "src.pkg.app",
            build_index({"src.pkg.app": source}, mapping),
            root,
        ),
    )


@pytest.mark.parametrize(
    "loader,name",
    [
        ("from dotenv import load_dotenv\nload_dotenv()\n", ".env"),
        ("from dotenv import dotenv_values\nvalues = dotenv_values()\n", ".env"),
        (
            'from dotenv import load_dotenv, find_dotenv\nload_dotenv(find_dotenv("settings.conf"))\n',
            "settings.conf",
        ),
        (
            'from dotenv import load_dotenv, find_dotenv\npath = find_dotenv(filename="settings.conf", usecwd=False)\nload_dotenv(path)\n',
            "settings.conf",
        ),
    ],
)
def test_refuses_when_search_finds_intermediate_endpoint(
    tmp_path: Path, loader: str, name: str
) -> None:
    # Given a clean root candidate and a nearer intermediate custom endpoint.
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / name).write_text("OTHER=value\n")
    (tmp_path / "src" / name).write_text("DAYTONA_API_URL=https://self.example/api\n")

    # When preflight follows the caller's upward search chain.
    items = inspect_loader(tmp_path, loader)

    # Then the clean root cannot suppress nearer endpoint evidence.
    assert [(item["file"], item["kind"]) for item in items] == [
        ("src/" + name, "env-api-url"),
        ("src/" + name, "custom-api-url"),
    ]


@pytest.mark.parametrize(
    "loader",
    [
        "from dotenv import load_dotenv\nload_dotenv(**options)\n",
        'from dotenv import load_dotenv\nload_dotenv("settings.conf", **options)\n',
        'from dotenv import load_dotenv, find_dotenv\nload_dotenv(find_dotenv("settings.conf", usecwd=True))\n',
        'from dotenv import load_dotenv, find_dotenv\nload_dotenv(find_dotenv("settings.conf", False, True))\n',
        'from dotenv import load_dotenv, find_dotenv\nload_dotenv(find_dotenv("settings.conf", **options))\n',
        "from dotenv import load_dotenv, find_dotenv\nload_dotenv(find_dotenv(filename))\n",
        "from dotenv import load_dotenv\nload_dotenv(stream=stream)\n",
        'from pydantic_settings import SettingsConfigDict\nconfig = SettingsConfigDict(env_file="settings.conf", **options)\n',
    ],
)
def test_reviews_when_search_inputs_are_unresolved(tmp_path: Path, loader: str) -> None:
    # Given clean plausible candidates that cannot prove the runtime selection.
    (tmp_path / ".env").write_text("OTHER=value\n")
    (tmp_path / "settings.conf").write_text("OTHER=value\n")

    # When a runtime-dependent loader is inspected.
    items = inspect_loader(tmp_path, loader)

    # Then even existing clean files cannot erase the required review.
    assert [item["kind"] for item in items] == ["env-api-url"]


@pytest.mark.parametrize("found", [False, True])
def test_reviews_only_when_search_can_continue_above_root(
    tmp_path: Path, found: bool
) -> None:
    # Given either a bounded clean search or no match within the selected root.
    (tmp_path / "src/pkg").mkdir(parents=True)
    if found:
        (tmp_path / "src/.env").write_text("OTHER=value\n")

    # When the default loader searches from its source directory.
    items = inspect_loader(tmp_path, "from dotenv import load_dotenv\nload_dotenv()\n")

    # Then only an unmatched search retains unknown ancestor input.
    assert [item["kind"] for item in items] == ([] if found else ["env-api-url"])


def test_scans_all_candidates_when_nearest_search_match_is_clean(
    tmp_path: Path,
) -> None:
    # Given a clean nearest file and a custom endpoint farther up the same search.
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/settings.conf").write_text("OTHER=value\n")
    (tmp_path / "settings.conf").write_text(
        "DAYTONA_API_URL=https://self.example/api\n"
    )
    loader = 'from dotenv import load_dotenv, find_dotenv\nload_dotenv(find_dotenv("settings.conf"))\n'

    # When conservative preflight visits the complete bounded chain.
    items = inspect_loader(tmp_path, loader)

    # Then the first match never suppresses another existing candidate.
    assert [item["kind"] for item in items] == ["env-api-url", "custom-api-url"]


@pytest.mark.parametrize(
    "argument",
    [
        '"override.conf"',
        '["clean.conf", "override.conf"]',
        '("clean.conf", "override.conf")',
    ],
)
@pytest.mark.parametrize("module", ["pydantic", "pydantic_settings"])
def test_refuses_without_writes_when_settings_constructor_overrides_env_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argument: str, module: str
) -> None:
    # Given a settings subclass with a clean config and a custom constructor input.
    source = (
        f"from {module} import BaseSettings\n"
        "class Settings(BaseSettings):\n    model_config = {'env_file': 'clean.conf'}\n"
        f"settings = Settings(_env_file={argument})\n" + CLIENT
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "clean.conf").write_text("OTHER=value\n")
    (tmp_path / "override.conf").write_text(
        "DAYTONA_API_URL=https://self.example/api\n"
    )
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("daytona==0.224.0\n")

    # When the actual CLI performs strict transactional preflight.
    status = main([str(tmp_path), "--yes", "--force", "--strict", "--json"])

    # Then neither source nor dependency is written before refusal.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["changedFiles"] == []
    assert app.read_text() == source
    assert requirement.read_text() == "daytona==0.224.0\n"


@pytest.mark.parametrize(
    "argument", ["selected", "**options", '"missing.conf"', '["clean.conf", selected]']
)
def test_reviews_when_settings_override_cannot_be_resolved(
    tmp_path: Path, argument: str
) -> None:
    # Given a proven inherited settings alias with runtime-dependent override input.
    (tmp_path / "clean.conf").write_text("OTHER=value\n")
    loader = (
        "from pydantic_settings import BaseSettings\n"
        "class Parent(BaseSettings): pass\nclass Settings(Parent): pass\n"
        "Alias = Settings\n"
        + (
            f"settings = Alias({argument})\n"
            if argument.startswith("**")
            else f"settings = Alias(_env_file={argument})\n"
        )
    )

    # When environment analysis follows the constructor's proven class binding.
    items = inspect_loader(tmp_path, loader)

    # Then missing and dynamic inputs cannot silently pass.
    assert [item["kind"] for item in items] == ["env-api-url"]


@pytest.mark.parametrize(
    "definition",
    [
        "class Settings: pass\n",
        "from pydantic_settings import BaseSettings\nclass Settings(BaseSettings): pass\nSettings = foreign\n",
    ],
)
def test_ignores_settings_keyword_when_class_is_unrelated_or_rebound(
    tmp_path: Path, definition: str
) -> None:
    # Given a spelling with no surviving settings-constructor proof.
    loader = definition + "settings = Settings(_env_file=selected)\n"

    # When the keyword is used on that unrelated callable.
    items = inspect_loader(tmp_path, loader)

    # Then spelling alone introduces no loader review.
    assert items == ()
