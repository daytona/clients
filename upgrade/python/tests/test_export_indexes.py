# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Import boundaries and signed indexes retain only justified receiver evidence."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project_index import build_index
from daytona_upgrade.resolution import ProjectIndex
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "declaration",
    ["import daytona as dt", "import daytona_sdk as dt"],
)
def test_migrates_client_when_type_only_module_is_imported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], declaration: str
) -> None:
    # Given two modules sharing a type-only namespace rather than a type alias.
    (tmp_path / "deps.py").write_text(
        f"from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    {declaration}\n"
    )
    source = "from deps import dt\ndef launch(client: dt.Daytona): client.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When resolving the consumer's qualified annotation through the CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the namespace still identifies a client without a heuristic FIXME.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source.replace("client.create()", "client.v0.create()")


@pytest.mark.parametrize(
    "declaration",
    ["import daytona as dt", "from daytona import common as dt"],
)
def test_suggests_resource_when_type_only_module_is_imported(declaration: str) -> None:
    # Given a resource annotation reached through another module's typing import.
    source = "from deps import dt\ndef launch(sandbox: dt.sandbox.Sandbox): sandbox.process.exec('ls')\n"
    mapping = load_mapping()
    sources = {
        "deps": "from typing import TYPE_CHECKING\n"
        f"if TYPE_CHECKING:\n    {declaration}\n",
        "app": source,
    }
    # When following resource evidence in suggestion mode across the import boundary.
    result = transform_source(
        source,
        TransformOptions(mapping, mode="suggest-v2"),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then the resource call is recognized rather than silently skipped.
    assert [item["kind"] for item in result.items] == ["suggestion"]


@pytest.mark.parametrize(
    "declaration",
    [
        "__all__ = exported_names()",
        "__all__ = list(('client', '_client'))",
        "__all__ = []\n__all__ += exported_names()",
        "__all__ = exported_names()\n__all__ += []",
        "__all__ = [] + exported_names()",
        "__all__ = exported_names() + []",
        "__all__ = []\n__all__.extend(exported_names())",
        "if condition:\n    __all__ = []",
    ],
)
def test_reviews_collisions_when_wildcard_exports_are_unresolved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], declaration: str
) -> None:
    # Given uncertain exports which could replace public and private client bindings.
    (tmp_path / "deps.py").write_text(
        "from httpx import Client\nclient = Client()\n_client = Client()\n"
        + declaration
        + "\n"
    )
    source = (
        "from daytona import Daytona\nclient = Daytona()\n_client = Daytona()\n"
        "keep = Daytona()\nfrom deps import *\n"
        "client.create()\n_client.create()\nkeep.create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When a star import has unknown membership, not an explicitly empty __all__.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then collisions need review without importing proofs or killing unrelated names.
    assert status == 1
    assert [item["kind"] for item in json.loads(capsys.readouterr().out)["items"]] == [
        "fixme",
        "fixme",
    ]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(
        ast.parse(source.replace("keep.create()", "keep.v0.create()"))
    )


@pytest.mark.parametrize(
    "declaration", ["__all__ = []", "__all__ = ()", "__all__ = []\n__all__ += []"]
)
def test_keeps_proof_when_wildcard_exports_are_explicitly_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], declaration: str
) -> None:
    # Given an export list proven to exclude the colliding binding.
    (tmp_path / "deps.py").write_text("client = unknown()\n" + declaration + "\n")
    source = "from daytona import Daytona\nclient = Daytona()\nfrom deps import *\nclient.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When importing the explicitly empty surface.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the local client proof remains valid.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source.replace("client.create()", "client.v0.create()")


@pytest.mark.parametrize(
    "annotation", ["tuple[Client, Daytona]", "tuple[Daytona, Client]"]
)
@pytest.mark.parametrize(
    "index", ["0", "1", "+0", "+1", "-0", "-1", "-2", "2", "+2", "-3"]
)
def test_selects_position_when_tuple_index_is_signed_literal(
    annotation: str, index: str
) -> None:
    # Given a heterogeneous tuple and a literal index with Python's signed syntax.
    source = (
        "from httpx import Client\nfrom daytona import Daytona\n"
        f"def launch(pair: {annotation}): pair[{index}].get('url')\n"
    )
    position = int(index)
    is_client = -2 <= position < 2 and (position % 2 == 0) == annotation.startswith(
        "tuple[Daytona"
    )
    # When selecting one position, including indexes from the end.
    result = migrate(source)
    # Then only the selected client-bearing slot requires review.
    assert [item["kind"] for item in result.items] == (["fixme"] if is_client else [])
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("index", ["True", "+True", "-True", "1.0", "+1.0", "offset"])
def test_reviews_tuple_when_index_is_not_integer_literal(index: str) -> None:
    # Given an index which must not be certified as a signed integer literal.
    source = (
        "from httpx import Client\nfrom daytona import Daytona\n"
        f"def launch(pair: tuple[Client, Daytona]): pair[{index}].get('url')\n"
    )
    # When selecting a position the bounded analyzer cannot prove.
    result = migrate(source)
    # Then uncertainty remains visible, with no executable rewrite.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_returns_immutable_names_when_all_is_explicit() -> None:
    # Given explicit membership overlapping only some module exports.
    index = ProjectIndex(
        {"deps": {"client": "client", "other": "non-client"}},
        wildcard_exports={"deps": frozenset({"client", "missing"})},
    )
    # When computing the wildcard surface.
    names = index.wildcard_names("deps")
    # Then the advertised immutable collection contract holds in this branch too.
    assert isinstance(names, frozenset)
    assert names == frozenset({"client"})


@pytest.mark.parametrize(
    "binding",
    [
        "from daytona import Daytona\nvalue = Daytona()\nCALL = value.create()",
        "from daytona import Daytona as C\nCALL = C().create()",
        "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\nCALL = make().create()",
        "from daytona import Daytona as C\nCALL = def launch(value: C): value.create()",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from daytona import Daytona as C\nCALL = def launch(value: C): value.create()",
    ],
)
def test_reviews_prior_proof_when_colliding_name_has_no_client_hint(
    binding: str,
) -> None:
    # Given definite local evidence, regardless of the receiver's spelling.
    declarations, call = binding.split("CALL = ")
    source = declarations + "from deps import *\n" + call + "\n"
    mapping = load_mapping()
    sources = {
        "deps": "value = unknown()\nC = unknown()\nmake = unknown()\n__all__ = exported_names()\n",
        "app": source,
    }
    # When the star import might replace that evidence with an unrelated binding.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then prior client evidence becomes a FIXME, never a rewrite or silent miss.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
