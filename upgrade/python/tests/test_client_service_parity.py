# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client method calls preserve service guidance without trusting escaped holders."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Final

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source

SOURCE: Final = """from daytona import Daytona
daytona = Daytona()
def ensure():
    daytona.snapshot.get("base")
    daytona.volume.get("data", True)
    daytona.secret.get("token")
def main():
    ensure()
    daytona.create()
"""


def guidance(source: str) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


def test_root_services_keep_guidance_when_direct_method_can_run_first() -> None:
    # Given deferred service calls and a direct call on the same root binding.
    source = SOURCE
    # When requesting guidance using the same proof index as the default rewrite.
    result = guidance(source)
    # Then all known calls retain suggestions without changing executable syntax.
    assert [
        item["suggestion"]["id"] for item in result.items if "suggestion" in item
    ] == [
        "client.snapshot.get",
        "client.volume.get",
        "client.secret.get",
        "client.create",
    ]
    assert all(item["kind"] == "suggestion" for item in result.items)
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_root_services_route_when_direct_method_can_run_first() -> None:
    # Given the same deferred service calls used in the suggestion regression.
    source = SOURCE
    # When performing the default upgrade via the existing flow test helper.
    result = migrate(source)
    # Then every proven moved member is routed exactly once.
    assert result.source == source.replace(
        "daytona.snapshot", "daytona.v0.snapshot"
    ).replace("daytona.volume", "daytona.v0.volume").replace(
        "daytona.secret", "daytona.v0.secret"
    ).replace("daytona.create", "daytona.v0.create")
    assert result.items == ()


@pytest.mark.parametrize("suggest", [False, True])
def test_cli_parity_when_legacy_requirement_pins_client_services(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], suggest: bool
) -> None:
    # Given a real 0.x project with a method call before service access.
    app = tmp_path / "app.py"
    app.write_text(SOURCE)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When invoking the selected public CLI mode.
    status = main(
        [str(tmp_path), "--json", "--strict", *(["--suggest-v2"] if suggest else [])]
    )
    # Then neither mode loses the service calls or introduces manual work.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    if suggest:
        assert [
            item["suggestion"]["id"] for item in report["items"] if "suggestion" in item
        ] == [
            "client.snapshot.get",
            "client.volume.get",
            "client.secret.get",
            "client.create",
        ]
        assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(SOURCE))
    else:
        assert app.read_text().count("daytona.v0.") == 4
        assert report["items"] == []


def test_holder_keeps_escape_rule_when_its_method_can_run_first() -> None:
    # Given a user-defined holder that can change its unannotated client field.
    source = (
        "from daytona import Daytona\nclass Service:\n"
        "    def __init__(self): self.client = Daytona()\n"
        "    def change(self): self.client = unrelated()\n"
        "service = Service()\nservice.change()\nservice.client.create()\n"
    )
    # When upgrading after the holder method call.
    result = migrate(source)
    # Then the uncertain field is not routed, unlike a direct root client.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_guidance_reports_when_known_client_receiver_loses_proof() -> None:
    # Given a holder that escapes after its client field was established.
    source = (
        "from daytona import Daytona\ndef run(holder):\n"
        "    holder.client = Daytona()\n    opaque(holder)\n"
        "    holder.client.create()\n"
    )
    # When requesting suggestions rather than executable rewrites.
    result = guidance(source)
    # Then the known but uncertain client call is still reported for review.
    assert [(item["kind"], item["line"]) for item in result.items] == [("fixme", 5)]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert "FIXME(daytona-upgrade)" in result.source


@pytest.mark.parametrize(
    "body",
    [
        "def run(client):\n    client.create()\n",
        "def run(clients):\n    clients['daytona'].create()\n",
        "from typing import Any\ndef run(client: Any):\n    client.create()\n",
    ],
)
def test_guidance_is_silent_when_receiver_has_no_client_evidence(body: str) -> None:
    # Given a generic receiver despite an unrelated Daytona import.
    source = "from daytona import Daytona\n" + body
    # When requesting guidance.
    result = guidance(source)
    # Then neither executable edits nor speculative review items are produced.
    assert result.source == source
    assert result.items == ()


def test_cli_strict_fails_when_suggestion_receiver_is_linked_but_uncertain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an escaping holder in an otherwise upgradeable legacy project.
    app = tmp_path / "app.py"
    source = (
        "from daytona import Daytona\ndef run(holder):\n"
        "    holder.client = Daytona()\n    opaque(holder)\n"
        "    holder.client.create()\n"
    )
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When the public CLI runs suggestion mode with strict diagnostics.
    status = main([str(tmp_path), "--json", "--strict", "--suggest-v2"])
    # Then the known call requires review but no executable rewrite is guessed.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["fixme"]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))


def test_guidance_is_silent_when_member_belongs_to_client_factory_function() -> None:
    # Given a function returning an uncertain client, not itself a client.
    source = (
        "from daytona import Daytona\ndef make(holder):\n"
        "    holder.client = Daytona()\n    opaque(holder)\n"
        "    return holder.client\n"
        "make.create = lambda: None\nmake.create()\n"
    )
    # When inspecting the unrelated callable attribute on that function.
    result = guidance(source)
    # Then return provenance does not identify the function as a client receiver.
    assert result.source == source
    assert result.items == ()


def test_volume_suggestions_flag_the_server_chosen_type() -> None:
    # Given 0.x volume calls, which always made disk volumes.
    source = 'from daytona import Daytona\ndaytona = Daytona()\ndaytona.volume.create("data")\ndaytona.volume.get("data", True)\n'
    # When requesting guidance.
    result = guidance(source)
    # Then both suggestions keep the call without a type and ask for a review of it.
    notes = {
        item["suggestion"]["id"]: item["suggestion"]["notes"]
        for item in result.items
        if "suggestion" in item
    }
    assert notes["client.volume.create"][-1] == (
        "Review the volume type: v0 volumes were always disk volumes, v2 lets the server choose, "
        'so pass type="disk" (with size_gib) to keep a disk volume'
    )
    assert notes["client.volume.get"][-1] == (
        "Review the volume type: v0 volumes were always disk volumes, create=True lets the server choose, "
        "so to keep a disk volume call volumes.get(name_or_id) and, when it raises NotFoundError, "
        'volumes.create(name=..., type="disk")'
    )
    assert "# daytona-v2:   differs: Review the volume type" in result.source
    assert all(item["kind"] == "suggestion" for item in result.items)


def test_code_run_suggestion_points_chart_artifacts_at_the_v2_result() -> None:
    # Given a 0.x code run whose chart artifacts the caller reads.
    source = (
        "from daytona import Daytona\n"
        "daytona = Daytona()\n"
        "sandbox = daytona.create()\n"
        'response = sandbox.process.code_run("plot()")\n'
        "print(response.artifacts.charts)\n"
    )
    # When requesting guidance.
    result = guidance(source)
    # Then the suggestion says where the charts and their images are in v2.
    notes = {
        item["suggestion"]["id"]: item["suggestion"]["notes"]
        for item in result.items
        if "suggestion" in item
    }
    assert notes["sandbox.process.codeRun"][-1] == (
        "Returns InterpreterResult rather than ExecuteResponse: response.artifacts.charts becomes result.charts, "
        "and each figure's image is in result.results[i].png"
    )
    assert (
        "# daytona-v2:   differs: Returns InterpreterResult rather than ExecuteResponse: response.artifacts.charts"
        in result.source
    )
