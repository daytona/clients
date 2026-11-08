# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared errors always keep the v0 constructor, fields and catch identity."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Final

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import Mode, SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source

# The 0.x root aliases of two shared categories. The 2.x root no longer
# exports them, but bundled v0 does, so they route to v0 like any other error.
FORMER_ROOT_ALIASES: Final = ("DaytonaValidationError", "DaytonaAuthorizationError")
ROOT_ERRORS: Final = tuple(
    name for name in load_mapping().root_names if name.endswith("Error")
)
ERRORS: Final = (*ROOT_ERRORS, *FORMER_ROOT_ALIASES)
FORMS: Final = (
    ("from {module} import {name}\n", "from daytona.v0 import {name}\n"),
    (
        "from {module} import {name} as Missing\n",
        "from daytona.v0 import {name} as Missing\n",
    ),
    (
        "from {module} import Daytona, DaytonaConfig, {name}\n",
        "from daytona import Daytona, DaytonaConfig\nfrom daytona.v0 import {name}\n",
    ),
    (
        "from {module} import Daytona, {name}; use({name})\n",
        "from daytona import Daytona; from daytona.v0 import {name}; use({name})\n",
    ),
    (
        "import {module}\nerror_type = {module}.{name}\n",
        "import daytona\nerror_type = daytona.v0.{name}\n",
    ),
    (
        "import {module} as sdk\nerror_type = sdk.{name}\n",
        "import daytona as sdk\nerror_type = sdk.v0.{name}\n",
    ),
    (
        'from unittest.mock import patch as mock\nmock("{module}.{name}")\n',
        'from unittest.mock import patch as mock\nmock("daytona.v0.{name}")\n',
    ),
    (
        'import {module} as sdk\nerror: "sdk.{name}"\n',
        'import daytona as sdk\nerror: "sdk.v0.{name}"\n',
    ),
)


def transform(source: str, mode: Mode = "upgrade") -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, mode),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize("name", ERRORS)
@pytest.mark.parametrize("module", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize("forms", FORMS)
def test_routes_to_v0_when_shared_error_has_any_supported_form(
    name: str, module: str, forms: tuple[str, str]
) -> None:
    # Given each shared error in each import, attribute, mock or annotation form.
    source, expected = (form.format(module=module, name=name) for form in forms)
    # When upgrading without requiring any construction or usage evidence.
    result = transform(source)
    # Then the error always resolves to v0 without manual-review items.
    assert result.source == expected
    assert result.items == ()


def test_upgrade_is_idempotent_when_shared_errors_already_use_v0() -> None:
    # Given the migrated forms for every shared error.
    source = "".join(
        expected.format(name=name) for name in ERRORS for _, expected in FORMS
    )
    # When upgrading again.
    result = transform(source)
    # Then no namespace is added twice and no review is introduced.
    assert result.source == source
    assert result.items == ()


def test_cli_moves_provider_when_project_reexports_unused_shared_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an unused alias re-exported through a project module.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    provider = tmp_path / "provider.py"
    provider.write_text("from daytona_sdk import DaytonaError as Failure\n")
    barrel = tmp_path / "barrel.py"
    barrel.write_text("from provider import Failure\n")
    consumer = tmp_path / "app.py"
    consumer.write_text("from barrel import Failure\n")
    # When the real CLI upgrades the whole project in strict mode.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the provider uses v0 regardless of downstream use; re-exports stay local.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert provider.read_text() == "from daytona.v0 import DaytonaError as Failure\n"
    assert barrel.read_text() == "from provider import Failure\n"
    assert consumer.read_text() == "from barrel import Failure\n"


@pytest.mark.parametrize("name", ERRORS)
def test_suggests_root_errors_when_v0_error_is_imported(name: str) -> None:
    # Given a legacy error import, including its local alias.
    source = f"from daytona.v0 import {name} as Failure\n"
    # When asking for guidance rather than changing SDK behavior.
    result = transform(source, "suggest-v2")
    # Then guidance attaches to the import without altering its code or identity.
    assert result.source.startswith("# daytona-v2:")
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("suggestion", 1)
    ]


@pytest.mark.parametrize("mode", ["suggest-v2", "remove-suggestions"])
def test_guidance_lifecycle_when_shared_error_suggestion_exists(mode: Mode) -> None:
    # Given tagged guidance on an import with several shared errors.
    source = "from daytona.v0 import DaytonaError, DaytonaNotFoundError\n"
    suggested = transform(source, "suggest-v2").source
    # When re-running guidance or removing it through the existing mechanism.
    result = transform(suggested, mode)
    # Then guidance is idempotent and removable without disturbing the import.
    assert result.source == (suggested if mode == "suggest-v2" else source)


def test_omits_error_guidance_when_v0_import_only_names_resources() -> None:
    # Given a resource import without a shared error.
    source = "from daytona.v0 import Sandbox\n"
    # When asking for v2 suggestions.
    result = transform(source, "suggest-v2")
    # Then no error-import guidance appears.
    assert result.source == source
    assert result.items == ()


def test_suggests_root_errors_when_v0_module_is_star_imported() -> None:
    # Given a star import, which can bring in the shared errors.
    source = "from daytona.v0 import *\n"
    # When asking for v2 guidance.
    result = transform(source, "suggest-v2")
    # Then the same error-import note attaches to the star import.
    assert result.source.startswith("# daytona-v2: when moving these calls to v2")
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("name", FORMER_ROOT_ALIASES)
def test_former_root_aliases_are_v0_exports_not_root_names(name: str) -> None:
    mapping = load_mapping()
    assert name not in mapping.root_names
    assert mapping.v0_exports is not None
    assert name in mapping.v0_exports


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("DaytonaValidationError", "DaytonaBadRequestError"),
        ("DaytonaAuthorizationError", "DaytonaForbiddenError"),
    ],
)
@pytest.mark.parametrize(
    "source",
    [
        "from daytona.v0 import {name}\n",
        "import daytona.v0 as sdk\nerror_type = sdk.{name}\n",
    ],
)
def test_former_root_aliases_get_guidance_naming_the_surviving_category(
    name: str, category: str, source: str
) -> None:
    result = transform(source.format(name=name), "suggest-v2")
    assert result.source.startswith("# daytona-v2:")
    assert f"{name} is {category} at the root" in result.source
    assert [item["kind"] for item in result.items] == ["suggestion"]
    assert f"{name} is {category} at the root" in result.items[0]["message"]
