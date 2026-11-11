# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A create that provably passes a 0.x Dockerfile image gets the build-context form."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SuggestionReport, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def suggestions(source: str) -> list[SuggestionReport]:
    mapping = load_mapping()
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    return [
        item["suggestion"]
        for item in result.items
        if "suggestion" in item and not item["suggestion"]["id"].startswith("workflow:")
    ]


def test_a_dockerfile_image_gets_the_build_context_form_as_in_typescript() -> None:
    # Given 0.x creates whose image is Image.from_dockerfile(...), directly,
    # through a name, through a module import and through the v0 module.
    found = suggestions(
        "from daytona import CreateSandboxFromImageParams, CreateSnapshotParams, Daytona, Image\n"
        "import daytona as sdk\n"
        "from daytona.v0 import Image as V0Image\n"
        "client = Daytona()\n"
        'client.snapshot.create(CreateSnapshotParams(name="web", image=Image.from_dockerfile("Dockerfile")))\n'
        'image = sdk.Image.from_dockerfile("app/Dockerfile")\n'
        "box = client.create(CreateSandboxFromImageParams(image=image))\n"
        'client.snapshot.create(params=CreateSnapshotParams(image=V0Image.from_dockerfile("D")))\n'
    )
    # When / Then each gets the build-context form and its notes.
    assert [(s["id"], s["v2"], s["category"]) for s in found] == [
        (
            "client.snapshot.create",
            "client.v2.images.build_from_context(context, dockerfile=dockerfile, name=name, region=region)",
            "differs",
        ),
        (
            "client.create",
            "client.v2.images.build_from_context(context, dockerfile=dockerfile, region=region)",
            "differs",
        ),
        (
            "client.snapshot.create",
            "client.v2.images.build_from_context(context, dockerfile=dockerfile, name=name, region=region)",
            "differs",
        ),
    ]
    for suggestion in found:
        assert any(
            "Image.from_dockerfile(path) uploaded the files its COPY and ADD lines name"
            in note
            for note in suggestion["notes"]
        )


@pytest.mark.parametrize(
    "call",
    [
        'client.snapshot.create(CreateSnapshotParams(name="web", image=Image.debian_slim("3.12")))',
        'client.snapshot.create(CreateSnapshotParams(image=Image.from_dockerfile("D"), **rest))',
        'image = Image.from_dockerfile("D")\nimage = other\nclient.create(CreateSandboxFromImageParams(image=image))',
        'client.snapshot.create(CreateSnapshotParams(name="web", image=Other.from_dockerfile("D")))',
        'client.create(CreateSnapshotParams(image=Image.from_dockerfile("D")))',
        'client.snapshot.create(mk(image=Image.from_dockerfile("D")))',
    ],
    ids=[
        "another builder",
        "a ** that may replace it",
        "a rebound name",
        "another package's Image",
        "the other call's parameters class",
        "a helper that may ignore its image",
    ],
)
def test_an_unproven_image_keeps_the_ordinary_form(call: str) -> None:
    # Given a create whose image is not provably a 0.x Dockerfile image.
    found = suggestions(
        "from daytona import CreateSandboxFromImageParams, CreateSnapshotParams, Daytona, Image\n"
        "from images import Image as Other\n"
        "def mk(image):\n"
        '    return CreateSnapshotParams(image=Image.base("python"))\n'
        "client = Daytona()\n" + call + "\n"
    )
    # Then its row keeps the ordinary form.
    assert len(found) == 1
    assert "build_from_context" not in (found[0]["v2"] or "")


ABSOLUTE_PATHS = (
    "v2 takes only absolute sandbox paths (starting with /) and refuses a relative one "
    "before any request; v0 resolved a relative path against the sandbox working directory"
)


def test_a_filesystem_call_notes_that_v2_paths_are_absolute_as_in_typescript() -> None:
    # Given a 0.x filesystem call on a proven sandbox.
    found = suggestions(
        "from daytona import Daytona\n"
        "client = Daytona()\n"
        "sandbox = client.create()\n"
        'sandbox.fs.list_files("workspace")\n'
    )
    # Then its suggestion carries the shared absolute-path note.
    [listing] = [s for s in found if s["id"] == "sandbox.fs.listFiles"]
    assert ABSOLUTE_PATHS in listing["notes"]
