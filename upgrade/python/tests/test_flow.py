# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Receiver proofs must follow Python bindings rather than suggestive names."""

from __future__ import annotations

import ast
from textwrap import dedent

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str) -> SourceResult:
    source = dedent(source).lstrip()
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize(
    "expression", ["Daytona().create()", "AsyncDaytona().create()"]
)
def test_direct_constructor_when_client_is_imported(expression: str) -> None:
    # Given a constructor whose binding is known.
    source = "from daytona import Daytona, AsyncDaytona\n" + expression + "\n"
    # When upgrading the source.
    result = migrate(source)
    # Then only the client member is namespaced.
    assert result.source == source.replace("().create()", "().v0.create()")


@pytest.mark.parametrize(
    "annotation",
    [
        "Daytona",
        "'Daytona'",
        "Optional[Daytona]",
        "Union[Daytona, None]",
        "Daytona | None",
        "'Daytona | None'",
    ],
)
def test_parameter_when_annotation_proves_client(annotation: str) -> None:
    # Given supported nullable and forward-reference annotations.
    source = f"from typing import Optional, Union\nfrom daytona import Daytona\ndef use(value: {annotation}):\n    return value.create()\n"
    # When upgrading the function.
    result = migrate(source)
    # Then the annotation proves the call independently of its name.
    assert result.source == source.replace("value.create()", "value.v0.create()")


@pytest.mark.parametrize(
    "body",
    [
        "value: Daytona = Daytona()\nvalue.create()",
        "with Daytona() as value:\n    value.create()",
        "async def run():\n    async with AsyncDaytona() as value:\n        await value.create()",
        "if (value := Daytona()):\n    value.create()",
        "def factory() -> Daytona:\n    return Daytona()\nvalue = factory()\nvalue.create()",
        "async def factory() -> AsyncDaytona:\n    return AsyncDaytona()\nasync def run():\n    value = await factory()\n    await value.create()",
        "class Factory:\n    def make(self) -> Daytona:\n        return Daytona()\nvalue = Factory().make()\nvalue.create()",
        "class Factory:\n    @classmethod\n    def make(cls) -> Daytona:\n        return Daytona()\nvalue = Factory.make()\nvalue.create()",
    ],
)
def test_binding_when_client_is_produced(body: str) -> None:
    # Given one independently specified producer of a root client.
    source = "from daytona import Daytona, AsyncDaytona\n" + body + "\n"
    # When upgrading it.
    result = migrate(source)
    # Then all calls on the proven value use v0.
    assert result.source == source.replace("value.create()", "value.v0.create()")


@pytest.mark.parametrize(
    "body",
    [
        "class Worker:\n    def run(self):\n        self.api.create()\n    def __init__(self):\n        self.api = Daytona()",
        "class Worker:\n    api: Daytona\n    def run(self):\n        self.api.create()",
        "class Worker:\n    def __init__(self):\n        self.api: 'Daytona'\n    def run(self):\n        self.api.create()",
    ],
)
def test_self_attribute_when_class_proves_client(body: str) -> None:
    # Given an attribute proved elsewhere in the same class.
    source = "from daytona import Daytona\n" + body + "\n"
    # When upgrading the class.
    result = migrate(source)
    # Then method order does not hide the proven receiver.
    assert result.source == source.replace("self.api.create()", "self.api.v0.create()")


@pytest.mark.parametrize(
    "body",
    [
        "value = Daytona()\nvalue = unrelated()\nvalue.create()",
        "def run(Daytona):\n    value = Daytona()\n    value.create()",
        "value = Daytona()\ndef run(value):\n    value.create()",
        "value = Daytona()\ndef run():\n    value.create()\n    value = unrelated()",
        "def inner():\n    value = Daytona()\nvalue.create()",
        "if condition:\n    value = Daytona()\nelse:\n    value = unrelated()\nvalue.create()",
        "class A:\n    def __init__(self):\n        self.api = Daytona()\nclass B:\n    def run(self):\n        self.api.create()",
        "value = Daytona()\nfor value in unknown:\n    value.create()",
        "value = Daytona()\nvalue, other = unknown\nvalue.create()",
        "Daytona = unrelated\nvalue = Daytona()\nvalue.create()",
        "def run():\n    value = Daytona()\n    return value\nvalue = run()\nvalue.create()",
        "class Daytona:\n    pass\nvalue = Daytona()\nvalue.create()",
    ],
)
def test_unknown_receiver_when_binding_is_shadowed_or_lost(body: str) -> None:
    # Given a spelling which does not prove the current receiver is Daytona.
    source = "from daytona import Daytona\n" + body + "\n"
    # When upgrading conservatively.
    result = migrate(source)
    # Then no executable call is guessed from an obsolete or different scope.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert "self.api.v0.create" not in result.source


def test_reassignment_when_only_earlier_call_is_proven() -> None:
    # Given one proven use followed by a binding kill.
    source = "from daytona import Daytona\nvalue = Daytona()\nvalue.create()\nvalue = unrelated()\nvalue.create()\n"
    # When upgrading sequentially.
    result = migrate(source)
    # Then the later call remains unchanged.
    assert (
        result.source
        == "from daytona import Daytona\nvalue = Daytona()\nvalue.v0.create()\nvalue = unrelated()\nvalue.create()\n"
    )


@pytest.mark.parametrize(
    "import_line,call",
    [
        ("from config import api", "api.create()"),
        ("import config as settings", "settings.api.create()"),
        ("from config import factory as make", "make().create()"),
        ("from .config import api", "api.create()"),
        ("from bridge import api", "api.create()"),
    ],
)
def test_cross_module_when_client_or_factory_is_imported(
    import_line: str, call: str
) -> None:
    # Given a project graph, including an indirect re-export.
    mapping = load_mapping()
    source = import_line + "\n" + call + "\n"
    sources = {
        "pkg.app": source,
        "config": "from daytona import Daytona\napi = Daytona()\ndef factory() -> Daytona:\n    return Daytona()\n",
        "pkg.config": "from daytona import Daytona\napi = Daytona()\n",
        "bridge": "from config import api\n",
    }
    # When upgrading the consumer with the complete project index.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("pkg/app.py", "pkg.app", build_index(sources, mapping)),
    )
    # Then imports carry proof without requiring an SDK import in the consumer.
    assert result.source == source.replace(".create()", ".v0.create()")


def test_close_and_explicit_namespaces_when_already_root_compatible() -> None:
    # Given already migrated methods and root lifecycle methods.
    source = "from daytona import Daytona\nvalue = Daytona()\nvalue.close()\nvalue.v0.create()\nvalue.v2.create('python:3.12')\n"
    # When upgrading again.
    result = migrate(source)
    # Then neither root close nor either explicit namespace is prefixed.
    assert result.source == source


def test_preserves_when_unannotated_parameter_has_only_generic_client_name() -> None:
    # Given a generic client name with no linked Daytona provenance.
    source = "from daytona import Daytona\ndef run(client):\n    client.create()\n"
    # When upgrading the unknown receiver.
    result = migrate(source)
    # Then an unrelated SDK import justifies neither a rewrite nor a manual item.
    assert result.source == source
    assert result.items == ()


def test_sandbox_when_unannotated_name_is_not_a_client() -> None:
    # Given a sandbox-like receiver with the same method name.
    source = "from daytona import Daytona\ndef run(sandbox_client):\n    sandbox_client.create()\n"
    # When upgrading it.
    result = migrate(source)
    # Then neither a rewrite nor a client FIXME is appropriate.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        "value = Daytona()\ndel value\nvalue.create()",
        "value = Daytona()\ntry:\n    risky()\nexcept Error as value:\n    value.create()",
        "value = Daytona()\nresult = lambda value: value.create()",
        "value = Daytona()\nresult = [value.create() for value in unknown]",
        "value = Daytona()\nresult = {value.create() for value in unknown}",
        "value = Daytona()\nresult = {key: value.create() for key, value in unknown}",
        "value = Daytona()\nresult = (value.create() for value in unknown)",
        "def run(*value):\n    value.create()",
        "value = Daytona()\ndef run(**value):\n    value.create()",
        "value = Daytona()\nwhile condition:\n    value = unrelated()\nvalue.create()",
        "value = Daytona()\ntry:\n    value = unrelated()\nexcept Error:\n    pass\nvalue.create()",
    ],
)
def test_unknown_receiver_when_python_scope_or_control_flow_loses_proof(
    body: str,
) -> None:
    # Given syntax that shadows, deletes, or may replace an earlier binding.
    source = "from daytona import Daytona\n" + body + "\n"
    # When upgrading conservatively rather than treating all stores as unconditional.
    result = migrate(source)
    # Then the uncertain call is never redirected to a different API.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "annotation", ["Daytona | Other", "Union[Daytona, Other]", "'Daytona | Other'"]
)
def test_unknown_receiver_when_union_includes_non_client(annotation: str) -> None:
    # Given a union which is not merely a nullable Daytona client.
    source = f"from typing import Union\nfrom daytona import Daytona\ndef run(value: {annotation}):\n    value.create()\n"
    # When analyzing a receiver that may be an unrelated object.
    result = migrate(source)
    # Then an annotation mentioning Daytona is insufficient proof.
    assert result.source == source


def test_unicode_when_receiver_position_uses_multibyte_prefix() -> None:
    # Given a non-ASCII identifier before the member on the same source line.
    source = "from daytona import Daytona\napi = Daytona()\né = 'ready'; api.create()\n"
    # When upgrading the expression.
    result = migrate(source)
    # Then source-position conversion does not miss or corrupt the call.
    assert result.source == source.replace("api.create()", "api.v0.create()")


@pytest.mark.parametrize(
    "member",
    [
        "create",
        "get",
        "list",
        "start",
        "stop",
        "delete",
        "snapshot",
        "volume",
        "secret",
        "warm_pool",
    ],
)
def test_detached_member_when_client_is_proven(member: str) -> None:
    # Given a member read rather than an immediate method invocation.
    source = f"from daytona import Daytona\napi = Daytona()\noperation = api.{member}\n"
    # When upgrading the reference.
    result = migrate(source)
    # Then bound-method and service references use the same v0 namespace.
    assert result.source == source.replace(f"api.{member}", f"api.v0.{member}")


@pytest.mark.parametrize("value", ["'https://private.example/api'", "url_from_env"])
def test_url_when_custom_or_unresolved_value_requires_refusal(value: str) -> None:
    # Given a configuration that cannot be proved to target Daytona Cloud.
    source = (
        f"from daytona import DaytonaConfig\nconfig = DaytonaConfig(api_url={value})\n"
    )
    # When calculating source edits before the project-wide write gate.
    result = migrate(source)
    # Then the URL remains and the refusal diagnostic identifies its original location.
    assert result.source == source
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("custom-api-url", 2)
    ]


@pytest.mark.parametrize(
    "value,expected,kind",
    [
        ("url_from_env", "DaytonaConfig()", "config-field"),
        (
            "'https://private.example/api'",
            "DaytonaConfig(api_url='https://private.example/api')",
            "custom-api-url",
        ),
    ],
)
def test_drop_assertion_when_url_is_literal_or_dynamic(
    value: str, expected: str, kind: str
) -> None:
    # Given the user's assertion, which only authorizes dropping nonliteral URLs.
    source = (
        f"from daytona import DaytonaConfig\nconfig = DaytonaConfig(api_url={value})\n"
    )
    mapping = load_mapping()
    # When applying --drop-api-url semantics to the source.
    result = transform_source(
        source,
        TransformOptions(mapping, drop_api_url=True),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then a known custom URL is still refused rather than silently redirected.
    assert result.source == f"from daytona import DaytonaConfig\nconfig = {expected}\n"
    assert [item["kind"] for item in result.items] == [kind]


@pytest.mark.parametrize(
    "operation,kind",
    [
        ("patch('daytona.Daytona.create')", "mock-target"),
        ("mock.patch('daytona.AsyncDaytona.get')", "mock-target"),
        ("mocker.patch('daytona.Daytona.snapshot.list')", "mock-target"),
        ("monkeypatch.setattr('daytona.Daytona.create', fake)", "mock-target"),
        ("importlib.import_module(module_name)", "dynamic-import"),
        ("__import__(module_name)", "dynamic-import"),
    ],
)
def test_manual_target_when_root_client_or_dynamic_import_is_used(
    operation: str, kind: str
) -> None:
    # Given a target whose correct runtime owner is not mechanically inferable.
    source = "from daytona import Daytona\n" + operation + "\n"
    # When inspecting the target during migration.
    result = migrate(source)
    # Then the unresolved target is preserved and explicitly reported.
    assert result.source == source
    assert [(item["kind"], item["line"]) for item in result.items] == [(kind, 2)]
