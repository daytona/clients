# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Optional SDK annotations require an unchanged module namespace."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "mutation",
    [
        'vars()["C"] = other',
        'locals()["C"] = other',
        'globals()["C"] = other',
        'module.__dict__["C"] = other',
        'namespace = module.__dict__; namespace["C"] = other',
        'setattr(module, "C", other)',
        "sys.modules[__name__].C = other",
        "module = sys.modules[__name__]; module.C = other",
        'builtins.vars()["C"] = other',
        'from builtins import vars as namespace; namespace()["C"] = other',
        'namespace = vars; namespace()["C"] = other',
    ],
)
def test_reviews_annotation_when_namespace_can_replace_import(mutation: str) -> None:
    # Given an optional import exposed to a writable module namespace.
    source = (
        "import sys, builtins\nmodule = sys.modules[__name__]\n"
        "try:\n    from daytona import Daytona as C\n"
        "except ImportError:\n    pass\n"
        f"{mutation}\n"
        "def make() -> C: return unknown()\n"
        "value = make()\nvalue.create()\n"
    )
    # When deciding whether the import is the annotation's only possible writer.
    result = migrate(source)
    # Then uncertainty is explicit and cannot authorize a namespace rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "suite",
    [
        "class X:\n    global C\n    C = Other\n",
        "class X:\n    class Y:\n        global C\n        C = Other\n",
        "class X:\n    def change(self):\n        global C\n        C = Other\n",
        "def outer():\n    class X:\n        global C\n        C = Other\n",
        "def outer():\n    class X:\n        def change(self):\n            global C\n            C = Another\n",
    ],
)
def test_reviews_annotation_when_class_suite_has_shared_writer(suite: str) -> None:
    # Given a global store hidden behind one or more lexical class scopes.
    source = (
        "try:\n    from daytona import Daytona as C\n"
        "except ImportError:\n    pass\n"
        + suite
        + "def make() -> C: return unknown()\nvalue = make()\nvalue.create()\n"
    )
    # When certifying the optional annotation across the complete source tree.
    result = migrate(source)
    # Then class-local traversal cannot hide a replacement of the module import.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


def test_keeps_import_proof_when_class_binding_is_local() -> None:
    # Given a class-local namesake which does not replace the module's import.
    source = (
        "try:\n    from daytona import Daytona as C\n"
        "except ImportError:\n    pass\nclass X:\n    C = Other\n"
        "def make() -> C: return unknown()\nvalue = make()\nvalue.create()\n"
    )
    # When scanning shared writes rather than flattening all lexical stores.
    result = migrate(source)
    # Then the independent module annotation remains provable.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


def test_keeps_proof_when_class_suite_writes_only_a_nonlocal_cell() -> None:
    # Given a nonlocal store that rebinds an enclosing function's cell, not the module import.
    source = (
        "try:\n    from daytona import Daytona as C\n"
        "except ImportError:\n    pass\n"
        "def outer():\n    C = Other\n    class X:\n        nonlocal C\n        C = Another\n"
        "def make() -> C: return unknown()\nvalue = make()\nvalue.create()\n"
    )
    # When deciding whether the import is the annotation's only possible writer.
    result = migrate(source)
    # Then the module-level import proof survives and the call is rewritten.
    assert "value.v0.create()" in result.source
    assert not any(item["kind"] == "fixme" for item in result.items)


def test_reviews_annotation_when_builtin_exec_is_aliased() -> None:
    # Given an aliased builtin exec that may rebind the imported name.
    source = (
        "from builtins import exec as run\n"
        "try:\n    from daytona import Daytona as C\n"
        "except ImportError:\n    pass\n"
        "run('C = object')\n"
        "def make() -> C: return unknown()\nvalue = make()\nvalue.create()\n"
    )
    # When certifying the import-only proof.
    result = migrate(source)
    # Then no rewrite is authorized, and the call still gets manual review.
    assert "value.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)
