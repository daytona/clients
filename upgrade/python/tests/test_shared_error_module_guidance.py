# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Module imports receive shared-error guidance only for proven SDK uses."""

from __future__ import annotations

import ast
from typing import Final

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import Mode, SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source

ERRORS: Final = tuple(
    name for name in load_mapping().root_names if name.endswith("Error")
)
IMPORTS: Final = (
    ("import daytona.v0 as sdk", "sdk"),
    ("import daytona.v0", "daytona.v0"),
    ("import daytona as sdk", "sdk.v0"),
    ("import daytona", "daytona.v0"),
)


def suggest(source: str, mode: Mode = "suggest-v2") -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, mode),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize("name", ERRORS)
@pytest.mark.parametrize("module_form", IMPORTS)
@pytest.mark.parametrize(
    "use",
    [
        "error_type = {binding}.{name}",
        "raise {binding}.{name}('missing')",
        "try:\n    work()\nexcept {binding}.{name}:\n    pass",
    ],
)
def test_guidance_when_module_binding_supplies_shared_error(
    name: str, module_form: tuple[str, str], use: str
) -> None:
    # Given a module import and a proven shared-error reference.
    declaration, binding = module_form
    source = declaration + "\n" + use.format(binding=binding, name=name) + "\n"
    expected = suggest(f"from daytona.v0 import {name}\n").items
    # When suggesting a v2 migration.
    result = suggest(source)
    # Then import guidance and report match the existing named-import behavior.
    assert result.items == expected
    assert result.source.startswith("# daytona-v2:")
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "import daytona.v0 as sdk\n",
        "import daytona.v0 as sdk\nresource = sdk.Sandbox\n",
        "import daytona.v2 as sdk\nerror_type = sdk.DaytonaError\n",
        "import daytona.v0 as sdk\nsdk = other\nerror_type = sdk.DaytonaError\n",
        "import daytona.v0\ndaytona = other\nerror_type = daytona.v0.DaytonaError\n",
        "import daytona.v0\ndaytona.v0 = other\nerror_type = daytona.v0.DaytonaError\n",
        "import daytona.v0 as sdk\nsdk.DaytonaError = other\nerror_type = sdk.DaytonaError\n",
        "import daytona.v0 as sdk\ndef run(sdk):\n    return sdk.DaytonaError\n",
        "import daytona.v0 as sdk\ndef run():\n    error_type = sdk.DaytonaError\n    sdk = other\n",
        "import daytona.v0 as sdk\nif condition:\n    sdk = other\nerror_type = sdk.DaytonaError\n",
    ],
)
def test_omits_guidance_when_module_error_reference_is_unproven(source: str) -> None:
    # Given an unused import, unrelated member or shadowed binding.
    # When suggesting a v2 migration.
    result = suggest(source)
    # Then no shared-error guidance is invented.
    assert result.items == ()
    assert result.source == source


def test_guidance_when_module_import_has_multiple_error_uses() -> None:
    # Given multiple errors from one import statement, including multiple aliases.
    source = (
        "import daytona.v0 as sdk, daytona.v0 as legacy\n"
        "errors = (sdk.DaytonaError, legacy.DaytonaNotFoundError)\n"
    )
    # When suggesting a v2 migration.
    result = suggest(source)
    # Then only one located import report is emitted.
    assert [(item["kind"], item["line"], item["column"]) for item in result.items] == [
        ("suggestion", 1, 1)
    ]
    assert result.source.count("# daytona-v2:") == 1


def test_guidance_when_same_import_alias_exists_in_independent_scopes() -> None:
    # Given an unused outer import and a local import that supplies the error.
    source = (
        "import daytona.v0 as sdk\n"
        "def run():\n"
        "    import daytona.v0 as sdk\n"
        "    return sdk.DaytonaError\n"
    )
    # When suggesting a v2 migration.
    result = suggest(source)
    # Then only the local import gets guidance.
    assert [(item["line"], item["column"]) for item in result.items] == [(3, 5)]


def test_guidance_when_module_binding_is_captured_by_function() -> None:
    # Given an error reference that captures a stable outer module import.
    source = "import daytona.v0 as sdk\ndef run():\n    return sdk.DaytonaError\n"
    # When suggesting a v2 migration.
    result = suggest(source)
    # Then guidance attaches to the captured import.
    assert [(item["line"], item["column"]) for item in result.items] == [(1, 1)]


def test_guidance_when_module_alias_is_imported_again() -> None:
    # Given one alias imported twice, but used only after the second import.
    source = (
        "import daytona.v0 as sdk\n"
        "sdk = other\n"
        "import daytona.v0 as sdk\n"
        "error_type = sdk.DaytonaError\n"
    )
    # When suggesting a v2 migration.
    result = suggest(source)
    # Then the unused earlier import does not receive guidance.
    assert [(item["line"], item["column"]) for item in result.items] == [(3, 1)]


@pytest.mark.parametrize("mode", ["suggest-v2", "remove-suggestions"])
def test_guidance_lifecycle_when_module_import_note_exists(mode: Mode) -> None:
    # Given an existing note on a module import.
    source = "import daytona.v0 as sdk\nerror_type = sdk.DaytonaError\n"
    suggested = suggest(source).source
    # When refreshing or removing guidance.
    result = suggest(suggested, mode)
    # Then the note is replaceable and executable code remains untouched.
    assert result.source == (suggested if mode == "suggest-v2" else source)


def test_omits_guidance_when_daytona_is_project_owned() -> None:
    # Given a project-local package rather than the SDK.
    source = "import daytona.v0 as sdk\nerror_type = sdk.DaytonaError\n"
    mapping = load_mapping()
    index = build_index({"app": source, "daytona": "", "daytona.v0": ""}, mapping)
    # When suggesting a v2 migration.
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", index),
    )
    # Then local module names do not certify SDK guidance.
    assert result.items == ()
    assert result.source == source
