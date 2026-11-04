# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency proofs, Docker scans and v2 receivers retain migration safety."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "mutation",
    [
        'REQS[:] = ["daytona==0.224.0"]',
        'REQS[0] = "daytona==0.224.0"',
        'REQS.clear()\nREQS.append("daytona==0.224.0")',
        'REQS.extend(["daytona==0.224.0"])',
        'alias = REQS\nalias[:] = ["daytona==0.224.0"]',
        "alias = REQS\nalias.clear()",
        'alias = REQS\nalias.append("daytona==0.224.0")',
        'alias = REQS\nalias.extend(["daytona==0.224.0"])',
        "alias = REQS\nif flag:\n    alias.clear()",
        "alias = REQS\ndel alias[0]",
        'alias = REQS\nalias += ["daytona==0.224.0"]',
    ],
)
def test_ancestor_stays_manual_when_requirement_list_is_mutated(
    tmp_path: Path, mutation: str
) -> None:
    # Given a parent declaration whose saved literal no longer proves its value.
    (tmp_path / ".git").mkdir()
    manifest = tmp_path / "setup.py"
    source = (
        'from setuptools import setup\nREQS = ["daytona[v0-sdk]>=2"]\n'
        + mutation
        + "\nsetup(install_requires=REQS)\n"
    )
    manifest.write_text(source)
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating only the consumer source.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then ancestor proof is withheld without executing or editing setup.py.
    assert any(item["kind"] == "manual-dependency" for item in result.items)
    assert manifest.read_text() == source


@pytest.mark.parametrize(
    "body",
    [
        "alias = REQS",
        "alias = REQS\nREQS = []\nREQS.clear()",
        "unrelated = []\nunrelated.clear()\nalias = REQS",
    ],
)
def test_ancestor_is_certified_when_requirement_alias_retains_its_value(
    tmp_path: Path, body: str
) -> None:
    # Given an unchanged installing list, even if its original name is rebound.
    (tmp_path / ".git").mkdir()
    (tmp_path / "setup.py").write_text(
        'from setuptools import setup\nREQS = ["daytona[v0-sdk]>=2"]\n'
        + body
        + "\nsetup(install_requires=alias)\n"
    )
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating the consumer source.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then rebinding and unrelated mutations do not destroy the saved alias proof.
    assert not any(item["kind"] == "manual-dependency" for item in result.items)


@pytest.mark.parametrize(
    "case",
    [
        (prefix, marker, closed)
        for prefix in ("RUN", "ONBUILD RUN", "COPY", "ADD")
        for marker in ("<<EOF", "<<-'EOF'", '<< "EOF"')
        for closed in (False, True)
    ],
)
def test_docker_scans_instructions_when_heredoc_is_unterminated(
    tmp_path: Path, case: tuple[str, str, bool]
) -> None:
    # Given payload-looking declarations and a real declaration after any closure.
    prefix, marker, closed = case
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile").write_text(
        f"{prefix} {marker}\nENV DAYTONA_API_URL=https://payload/api\n"
        + ("EOF\n" if closed else "")
        + "ARG DAYTONA_SERVER_URL=https://later/api\n"
    )
    # When preflight scans Docker instructions without evaluating the shell.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then only closed payloads are ignored; later declarations are always visible.
    assert [(item["kind"], item["line"]) for item in result.items] == (
        [] if closed else [("env-api-url", 2), ("custom-api-url", 2)]
    ) + [("env-api-url", 4 if closed else 3), ("custom-api-url", 4 if closed else 3)]


@pytest.mark.parametrize("command", ["RUN", "ONBUILD RUN"])
@pytest.mark.parametrize("closed", [False, True])
def test_docker_scans_all_payloads_when_any_heredoc_is_unterminated(
    tmp_path: Path, command: str, closed: bool
) -> None:
    # Given two heredocs, the second optionally unterminated, and a later ENV.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile").write_text(
        f"{command} <<FIRST <<SECOND\n"
        "ENV DAYTONA_API_URL=https://first/api\nFIRST\n"
        "ARG DAYTONA_SERVER_URL=https://second/api\n"
        + ("SECOND\n" if closed else "")
        + "ENV DAYTONA_API_URL=https://later/api\n"
    )
    # When scanning without certifying an incomplete heredoc group.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then any missing delimiter exposes the entire remainder as instructions.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        (kind, line)
        for line in ([6] if closed else [2, 4, 5])
        for kind in ("env-api-url", "custom-api-url")
    ]


@pytest.mark.parametrize("receiver", ["Daytona()", "factory()", "clients[0]", "api"])
@pytest.mark.parametrize("method", ["create", "snapshot.list"])
def test_suggestion_preserves_root_expression_when_v0_is_explicit(
    receiver: str, method: str
) -> None:
    # Given an explicit namespace on an expression, not a detached v0 alias.
    source = (
        "from daytona import Daytona\napi = Daytona()\n"
        + receiver
        + ".v0."
        + method
        + "()\n"
    )
    mapping = load_mapping()
    # When emitting comment-only guidance.
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then suggested code retains the receiver tree and replaces only its namespace.
    items = [item for item in result.items if "suggestion" in item]
    assert len(items) == 1
    assert items[0]["kind"] == "suggestion"
    form = items[0]["suggestion"]["v2"]
    assert form is not None
    expression = ast.parse(form, mode="eval").body
    assert isinstance(expression, ast.Call)
    function = expression.func
    assert isinstance(function, ast.Attribute)
    namespace = function.value
    if method == "snapshot.list":
        assert isinstance(namespace, ast.Attribute)
        namespace = namespace.value
    assert isinstance(namespace, ast.Attribute)
    assert namespace.attr == "v2"
    assert ast.dump(namespace.value) == ast.dump(ast.parse(receiver, mode="eval").body)
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_suggestion_ignores_v0_attribute_chains_in_files_without_daytona(
    tmp_path: Path,
) -> None:
    # Given an unrelated object exposing a `.v0` member with a mapped method name.
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "app.py").write_text(
        "class Api:\n    def run(self):\n        self.v0.create()\n"
    )
    # When writing v2 suggestions.
    code = main([str(tmp_path), "--suggest-v2", "--force", "--yes"])
    # Then the file is untouched.
    assert code == 0
    assert (
        tmp_path / "app.py"
    ).read_text() == "class Api:\n    def run(self):\n        self.v0.create()\n"
