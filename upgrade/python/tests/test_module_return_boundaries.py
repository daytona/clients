# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Module return proofs stop at uncertain bindings and foreign receivers."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "provider",
    [
        "def load():\n    import daytona\n    return None\n",
        "def load():\n    def nested():\n        import daytona\n        return daytona\n    return None\n",
        "def load():\n    return 'daytona'\n",
        "def load():\n    import other as daytona\n    return daytona\n",
        "def load():\n    import importlib\n    importlib = other\n    return importlib.import_module('daytona')\n",
    ],
)
def test_preserves_foreign_receiver_when_factory_has_no_module_identity(
    provider: str,
) -> None:
    # Given a name or import spelling which does not establish module identity.
    source = provider + "value = load()\nvalue.common.errors.X\n"
    # When checking the return rather than guessing from the member name.
    result = migrate(source)
    # Then unrelated private paths are neither rewritten nor annotated.
    assert result.source == source
    assert not any(item["kind"] in {"fixme", "private-import"} for item in result.items)


@pytest.mark.parametrize(
    "source",
    [
        "import daytona\n@opaque\ndef load():\n    return daytona\nvalue = load()\nvalue.common.errors.X\n",
        "import daytona\ndef load():\n    return daytona\ndef change():\n    global daytona\n    daytona = other\nvalue = load()\nvalue.common.errors.X\n",
        "import daytona\ndef load(module=daytona):\n    return module\nvalue = load()\nvalue.common.errors.X\n",
        "import daytona\ndef load(flag):\n    if flag:\n        import daytona as sdk\n    return sdk\nvalue = load(True)\nvalue.common.errors.X\n",
        "import daytona\ndef load():\n    return daytona\nvalue = load() if flag else None\nvalue.common.errors.X\n",
        "import daytona\nfrom foreign import Wrapper\nvalue = Wrapper(daytona)\nvalue.common.errors.X\n",
    ],
)
def test_reviews_private_path_when_module_proof_crosses_uncertain_boundary(
    source: str,
) -> None:
    # Given known Daytona identity with a decorator, writer or caller-owned value.
    # When the proof is demoted by the ordinary binding and factory machinery.
    result = migrate(source)
    # Then uncertainty stays visible without certifying the private receiver.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert result.items[0]["line"] == len(source.splitlines())


def test_reviews_cross_module_access_when_imported_factory_has_mixed_returns() -> None:
    # Given a consumer that cannot prove its imported helper always returns Daytona.
    source = "from provider import load\nload(True).common.errors.X\n"
    mapping = load_mapping()
    sources = {
        "app": source,
        "provider": "import daytona\ndef load(flag):\n    if flag:\n        return daytona\n    return None\n",
    }
    # When the index publishes the provider's uncertain return summary.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then imports preserve review evidence rather than silently discarding it.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_proves_factory_when_all_reaching_returns_are_module_aliases() -> None:
    # Given multiple returns of the same module and a nested unrelated return.
    source = (
        "import daytona as sdk\ndef load(flag):\n"
        "    def nested():\n        return None\n"
        "    if flag:\n        return sdk\n    return sdk\n"
        "load(True).common.errors.X\n"
    )
    # When collecting only this function's own reachable results.
    result = migrate(source)
    # Then nested returns do not demote an otherwise complete module proof.
    assert result.source == source.replace("load(True).common", "load(True).v0.common")
    assert result.items == ()
