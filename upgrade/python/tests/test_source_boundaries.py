# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Physical rows and import trivia retain their source ownership."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.lexical import magic_lines, source_lines
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def upgrade(source: str) -> SourceResult:
    mapping = load_mapping()
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    return transform_source(source, TransformOptions(mapping), context)


def test_magic_detection_when_cells_use_carriage_returns() -> None:
    # Given CR-delimited lines with both string data and executable magic.
    source = 'text = """hello\r%pip install data\r"""\r%pip install daytona\r'
    # When finding executable magic rows.
    rows = magic_lines(source)
    # Then only the physical fourth row is executable and terminators survive.
    assert rows == frozenset({4})
    assert source_lines(source) == [
        'text = """hello\r',
        "%pip install data\r",
        '"""\r',
        "%pip install daytona\r",
    ]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\v", "\f"])
def test_receiver_position_when_string_contains_unicode_separator(
    separator: str,
) -> None:
    # Given a physical second row whose byte columns differ from character columns.
    source = f'from daytona import Daytona\ntext = "{separator}"; client = Daytona(); client.create()\n'
    # When matching CST positions to AST receiver proofs.
    result = upgrade(source)
    # Then the proven call migrates despite the earlier string separator.
    assert result.source == source.replace("client.create()", "client.v0.create()")


@pytest.mark.parametrize("first", ["Daytona", "Sandbox"])
def test_import_trivia_when_root_and_resource_groups_split(first: str) -> None:
    # Given opening comments associated with the original first imported alias.
    second = "Sandbox" if first == "Daytona" else "Daytona"
    source = (
        f"from daytona import (\n    # opening note\n    {first},\n    {second},\n)\n"
    )
    # When splitting the import into root and resource groups.
    result = upgrade(source)
    # Then the comment appears once, on the group containing the first alias.
    assert result.source.count("# opening note") == 1
    imports = ast.parse(result.source).body
    owner = next(
        node
        for node in imports
        if isinstance(node, ast.ImportFrom) and node.names[0].name == first
    )
    assert (
        owner.lineno
        < result.source.splitlines().index("    # opening note") + 1
        < (owner.end_lineno or 0)
    )


@pytest.mark.parametrize("suffix", ["v0helpers", "v2helpers", "v0helpers.api"])
@pytest.mark.parametrize("root", ["daytona", "daytona_sdk"])
def test_private_import_when_version_prefix_is_not_a_namespace(
    root: str, suffix: str
) -> None:
    # Given a private module with a version-like prefix, not an exact namespace.
    source = f"from {root}.{suffix} import Helper\n"
    # When migrating the module path.
    result = upgrade(source)
    # Then the entire private suffix moves under the real v0 namespace.
    assert result.source == f"from daytona.v0.{suffix} import Helper\n"


@pytest.mark.parametrize(
    "later_write", ["make = other_factory\n", "def make():\n    return Other()\n"]
)
def test_factory_proof_when_forward_declaration_is_actually_rebound(
    later_write: str,
) -> None:
    # Given a formerly annotated factory with a later assignment or redefinition.
    source = (
        "from daytona import Daytona\n"
        "def make() -> Daytona:\n    return Daytona()\n"
        "def run():\n    client = make()\n    client.create()\n" + later_write
    )
    # When stable forward factories are distinguished from actual later writes.
    result = upgrade(source)
    # Then rebinding still kills the proof rather than producing a false rewrite.
    assert "client.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
