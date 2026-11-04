# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Uncertain collection elements and reflective stores require manual review."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade import cli
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("selection", ["pool[1:]", "pool[::-1]", "pool[:][:]"])
@pytest.mark.parametrize("consumer", ["loop", "comprehension", "index"])
def test_client_review_when_augmented_sequence_is_sliced(
    selection: str, consumer: str
) -> None:
    # Given a client-bearing sequence without an exact element proof.
    call = {
        "loop": f"for c in {selection}:\n    c.create()",
        "comprehension": f"[c.create() for c in {selection}]",
        "index": f"{selection}[0].create()",
    }[consumer]
    source = f"from daytona import Daytona\nd = Daytona()\npool = []\npool += [d, d]\n{call}\n"
    # When consuming a slice rather than a scalar element.
    result = migrate(source)
    # Then client evidence survives but never certifies an automatic rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("index", ["0", "-1", "position"])
def test_client_review_when_augmented_sequence_selects_element(index: str) -> None:
    # Given the existing uncertain element-index behavior.
    source = f"from daytona import Daytona\npool = []\npool += [Daytona()]\npool[{index}].create()\n"
    # When indexing rather than slicing.
    result = migrate(source)
    # Then no uncertain element is promoted to a proven client.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "element",
    [
        "d if flag else Other()",
        "Other() if flag else d",
        "d if flag else (Other() if ready else d)",
    ],
)
@pytest.mark.parametrize(
    "literal", ["[{element}]", "({element},)", "[{element}, Other()]"]
)
def test_client_review_when_augmented_literal_contains_conditional_client(
    element: str, literal: str
) -> None:
    # Given a literal whose element can be a client or an unrelated value.
    source = (
        "from daytona import Daytona\nd = Daytona()\npool = []\npool += "
        + literal.format(element=element)
        + "\npool[0].create()\n"
    )
    # When accumulating conditional client evidence.
    result = migrate(source)
    # Then uncertainty is reported rather than silently dropped or rewritten.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "attribute,write",
    [
        ("setattr", 'builtins.setattr(self, "c", Daytona())'),
        ("vars", 'builtins.vars(self)["c"] = Daytona()'),
        ("vars", "builtins.vars(self).update(c=Daytona())"),
        ("object", 'object.__setattr__(self, "c", Daytona())'),
        ("object", 'builtins.object.__setattr__(self, "c", Daytona())'),
    ],
)
@pytest.mark.parametrize("scope", ["local", "class"])
def test_client_stays_unproven_when_qualified_builtin_is_overwritten(
    attribute: str, write: str, scope: str
) -> None:
    # Given a replacement which does not perform the claimed attribute write.
    prefix = (
        "from daytona import Daytona\nimport builtins\n"
        "class Other:\n    def create(self):\n        return 'external'\n"
        "class NoOp:\n    @staticmethod\n    def __setattr__(*args):\n        pass\n"
        "replacement = lambda *args, **kwargs: {}\n"
        f"builtins.{attribute} = {'NoOp' if attribute == 'object' else 'replacement'}\n"
    )
    body = (
        f"def run(self):\n    self.c = Other()\n    {write}\n    self.c.create()\n"
        if scope == "local"
        else "class Owner:\n    def __init__(self):\n        "
        + write
        + "\n    def run(self):\n        self.c.create()\n"
    )
    source = prefix + body
    # When the matching builtin attribute has a project-wide write.
    result = migrate(source)
    # Then the no-op setter cannot manufacture a client-field proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items if item["kind"] == "fixme"] == [
        "fixme"
    ]


@pytest.mark.parametrize(
    "element", ["Other() if d else Other()", "Other() if flag else unknown()"]
)
def test_unrelated_elements_stay_quiet_when_client_is_only_a_condition(
    element: str,
) -> None:
    # Given no client in either value branch, even if the condition uses one.
    source = f"from daytona import Daytona\nd = Daytona()\npool = []\npool += [{element}]\npool[0].create()\n"
    # When accumulating nonclient elements.
    result = migrate(source)
    # Then the conditional predicate does not manufacture client provenance.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "attribute,write",
    [
        ("setattr", 'builtins.setattr(self, "c", Daytona())'),
        ("vars", 'builtins.vars(self)["c"] = Daytona()'),
        ("object", 'builtins.object.__setattr__(self, "c", Daytona())'),
    ],
)
@pytest.mark.parametrize("matching_write", [False, True])
def test_reflective_proof_when_builtin_is_overwritten_in_another_module(
    attribute: str, write: str, matching_write: bool
) -> None:
    # Given project-wide mutation evidence with no local builtin shadowing.
    source = f"from daytona import Daytona\nimport builtins\ndef run(self):\n    {write}\n    self.c.create()\n"
    replacement = (
        "import builtins\nbuiltins."
        + (attribute if matching_write else "len")
        + " = custom\n"
    )
    mapping = load_mapping()
    context = SourceContext(
        "app.py", "app", build_index({"app": source, "patches": replacement}, mapping)
    )
    # When migrating against the shared builtin-write index.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then only the matching overwritten builtin prevents an automatic proof.
    if matching_write:
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert [item["kind"] for item in result.items] == ["fixme"]
    else:
        assert result.source == source.replace("self.c.create()", "self.c.v0.create()")
        assert result.items == ()


def test_strict_cli_requires_review_when_conditional_client_is_accumulated(
    tmp_path: Path,
) -> None:
    # Given a project using the reported conditional literal.
    source = "from daytona import Daytona\nd = Daytona()\npool = []\npool += [d if flag else Other()]\npool[0].create()\n"
    path = tmp_path / "app.py"
    path.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When upgrading through the real strict CLI.
    status = cli.main([str(tmp_path), "--force", "--yes", "--strict"])
    # Then manual review is required and the uncertain call stays unchanged.
    assert status == 1
    assert ast.dump(ast.parse(path.read_text())) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "write",
    [
        'builtins.setattr(self, "pool", [Daytona()])',
        'builtins.vars(self)["pool"] = [Daytona()]',
        'builtins.object.__setattr__(self, "pool", [Daytona()])',
    ],
)
def test_collection_evidence_stays_reviewable_when_builtin_is_overwritten(
    write: str,
) -> None:
    # Given a shadowed reflective builtin storing a client-bearing collection.
    attribute = write.split("(")[0].split(".")[1]
    source = (
        "from daytona import Daytona\nimport builtins\n"
        f"builtins.{attribute} = lambda *args, **kwargs: {{}}\n"
        f"def run(self):\n    {write}\n    self.pool[0].create()\n"
    )
    # When migrating.
    result = migrate(source)
    # Then the element call is neither rewritten nor silently dropped.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items if item["kind"] == "fixme"] == [
        "fixme"
    ]


@pytest.mark.parametrize(
    "shadow",
    [
        "def vars(_):\n    return pool\n",
        "import builtins\nbuiltins.vars = lambda _: pool\n",
    ],
)
def test_shadowed_vars_store_still_fences_container_proofs(shadow: str) -> None:
    # Given a custom vars returning a client collection that the store mutates.
    source = (
        "from daytona import Daytona\n"
        "class Other:\n    def create(self):\n        return 'external'\n"
        f"pool = [Daytona()]\n{shadow}"
        "def run(owner):\n"
        "    vars(owner)[0] = Other()\n"
        "    pool[0].create()\n"
    )
    # When migrating.
    result = migrate(source)
    # Then the stale element proof is not used for a rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_qualified_builtin_vars_store_ignores_unrelated_vars_binding() -> None:
    # Given an unrelated module-level vars binding and a qualified builtin store.
    source = (
        "from daytona import Daytona\nimport builtins\n"
        "def helper(vars):\n    return vars\n"
        "def run(self):\n"
        '    builtins.vars(self)["pool"] = [Daytona()]\n'
        "    self.pool[0].create()\n"
    )
    # When migrating.
    result = migrate(source)
    # Then the qualified builtin store keeps its proof.
    assert result.source == source.replace(
        "self.pool[0].create()", "self.pool[0].v0.create()"
    )
