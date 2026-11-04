# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Container writes must not preserve homogeneous-element receiver proofs."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "mutation",
    [
        "alias[0] = Other()",
        "alias[:] = [Other()]",
        "alias.append(Other())",
        "alias.extend([Other()])",
        "alias.insert(0, Other())",
        "alias += [Other()]",
        "del alias[:]",
        "alias.clear()",
        "alias.reverse()",
        "alias.sort()",
        "alias.pop()",
        "alias.remove(unknown)",
    ],
)
@pytest.mark.parametrize("alias", ["clients", "alias", "holder.clients"])
def test_client_is_unproven_when_list_or_alias_mutates(
    mutation: str, alias: str
) -> None:
    # Given a homogeneous list and both direct and attribute aliases.
    source = (
        "from daytona import Daytona\nclients = [Daytona()]\n"
        "alias = clients\nholder.clients = clients\n"
        + mutation.replace("alias", alias)
        + "\nclients[0].create()\nalias[0].create()\nholder.clients[0].create()\n"
    )
    # When migrating after a mutation through any alias.
    result = migrate(source)
    # Then no alias retains a proof that every element is a client.
    assert ".v0.create()" not in result.source


@pytest.mark.parametrize(
    "body",
    [
        "def run():\n    clients[0].create()\nalias.append(Other())",
        "run = lambda: clients[0].create()\nalias[0] = Other()",
        "def replace():\n    alias.append(Other())\nreplace()\nclients[0].create()",
        "replace = lambda: alias.append(Other())\nreplace()\nclients[0].create()",
        "for unused in values:\n    clients[0].create()\n    alias.append(Other())",
        "while condition:\n    clients[0].create()\n    alias[:] = [Other()]",
        "[alias.append(Other()) for unused in values]\nclients[0].create()",
        "if condition:\n    alias.append(Other())\nclients[0].create()",
        "try:\n    alias.append(Other())\nexcept Error:\n    clients[0].create()",
        "[client.create() for client in clients if alias.append(Other())]",
        "calls = (client.create() for client in clients)\nalias.append(Other())\nlist(calls)",
    ],
)
def test_client_is_unproven_when_list_mutation_crosses_control_flow(body: str) -> None:
    # Given aliases used across deferred or repeated execution boundaries.
    source = (
        "from daytona import Daytona\nclients = [Daytona()]\nalias = clients\n"
        + body
        + "\n"
    )
    # When computing receiver proofs for every possible execution order.
    result = migrate(source)
    # Then uncertain list contents never cause an executable rewrite.
    assert ".v0.create()" not in result.source


def test_client_stays_proven_when_list_has_no_mutations() -> None:
    # Given homogeneous lists and an unrelated standalone client.
    source = "from daytona import Daytona\nclients = [Daytona()]\nalias = clients\nalias[0].create()\n"
    # When migrating an immutable use of the list.
    result = migrate(source)
    # Then the list still proves its element type.
    assert result.source == source.replace("alias[0].create()", "alias[0].v0.create()")


def test_standalone_client_stays_proven_when_container_mutates() -> None:
    # Given a client which is not inferred from container contents.
    source = "from daytona import Daytona\nclient = Daytona()\nclients = [Daytona()]\nclients.append(Other())\nclient.create()\n"
    # When a separate container loses its homogeneous-element proof.
    result = migrate(source)
    # Then the standalone client still migrates.
    assert "client.v0.create()" in result.source


@pytest.mark.parametrize(
    "mutation", ["self.clients[0] = Other()", "self.clients.append(Other())"]
)
def test_client_is_unproven_when_class_container_can_mutate(mutation: str) -> None:
    # Given class-wide list proofs and a mutation in a separate method.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self): self.clients = [Daytona()]\n"
        f"    def replace(self): {mutation}\n"
        "    def run(self): self.clients[0].create()\n"
    )
    # When joining the class's possible writes.
    result = migrate(source)
    # Then the initializer cannot overrule the mutation.
    assert ".v0.create()" not in result.source


def test_client_is_unproven_when_module_container_mutates() -> None:
    # Given a list reached through a module namespace, not a local list binding.
    mapping = load_mapping()
    sources = {
        "settings": "from daytona import Daytona\nclients = [Daytona()]\n",
        "app": "import settings\nsettings.clients.append(Other())\nsettings.clients[0].create()\n",
    }
    # When migrating with imported list proofs.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then index fallback cannot revive a mutated container's element proof.
    assert ".v0.create()" not in result.source


@pytest.mark.parametrize(
    "expression",
    [
        "clients[clients.append(Other()) or -1].create()",
        "operation = clients[alias.insert(0, Other()) or 0].create",
    ],
)
def test_client_is_unproven_when_index_evaluation_mutates_list(expression: str) -> None:
    # Given an index expression that changes the very contents being indexed.
    source = (
        "from daytona import Daytona\nclients = [Daytona()]\nalias = clients\n"
        + expression
        + "\n"
    )
    # When recording proofs for the containing expression.
    result = migrate(source)
    # Then pre-evaluation inference cannot survive a nested write.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
