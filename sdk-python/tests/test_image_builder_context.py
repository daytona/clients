# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared ADR 0006 cases and local-source archive boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter
from test_build_context_upload import storage_peer
from test_image_context_upload import build_builder
from typing_extensions import assert_never

from daytona.v2 import BadRequestError, ImageBuilder
from daytona.v2._image_context import write_builder_context
from daytona.v2._image_text import dockerfile_escape, ends_with_continuation

Step = (
    tuple[Literal["file", "dir"], str, str]
    | tuple[Literal["requirements", "workdir", "user"], str]
    | tuple[Literal["cmd", "entrypoint", "run", "pip"], list[str]]
    | tuple[Literal["env"], dict[str, str]]
)


@dataclass(frozen=True, slots=True)
class Source:
    registry: str | None = None
    dockerfile: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextCase:
    name: str
    files: dict[str, str]
    symlinks: dict[str, str] = field(default_factory=dict)
    source: Source
    steps: list[Step]
    dockerfile: str = ""
    entries: list[str] | None = None
    contents: dict[str, str] = field(default_factory=dict)
    error: str = ""
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class ContextCases:
    cases: list[ContextCase]
    refused: list[ContextCase]
    buildRefused: list[ContextCase]


CASES = TypeAdapter(ContextCases).validate_json(
    (Path(__file__).parent / "fixtures/image-builder-context.json").read_text(encoding="utf-8")
)


def make_builder(case: ContextCase, root: Path) -> ImageBuilder:
    for name, content in case.files.items():
        tree_path = root / name
        tree_path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith("/"):
            tree_path.mkdir(exist_ok=True)
        else:
            tree_path.write_text(content, encoding="utf-8")
            tree_path.chmod(0o644)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    for name, target in case.symlinks.items():
        (root / name).symlink_to(target)
    builder = (
        ImageBuilder.from_registry(case.source.registry)
        if case.source.registry is not None
        else ImageBuilder.from_dockerfile(case.source.dockerfile or "")
    )
    for step in case.steps:
        match step:
            case ("file", path, remote):
                builder = builder.add_local_file(root / path if path.strip() else path, remote)
            case ("dir", path, remote):
                builder = builder.add_local_dir(root / path if path.strip() else path, remote)
            case ("requirements", path):
                builder = builder.pip_install_from_requirements(root / path if path.strip() else path)
            case ("workdir", path):
                builder = builder.workdir(path)
            case ("user", name):
                builder = builder.user(name)
            case ("cmd", args):
                builder = builder.cmd(*args)
            case ("entrypoint", args):
                builder = builder.entrypoint(*args)
            case ("run", args):
                builder = builder.run_commands(*args)
            case ("pip", args):
                builder = builder.pip_install(*args)
            case ("env", values):
                builder = builder.env(values)
            case unreachable:
                assert_never(unreachable)
    return builder


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES.cases, ids=lambda case: case.name)
async def test_shared_context_renders_and_archives(case: ContextCase, tmp_path: Path) -> None:
    # Given the tree and complete builder chain from the cross-language fixture.
    builder = make_builder(case, tmp_path)
    destination = tmp_path / "context.tar"
    # When the builder is rendered and its context is built.
    assert builder.to_dockerfile() == case.dockerfile
    if case.entries is None:
        with storage_peer() as peer:
            await build_builder(peer, builder, False)
        # Then a builder without sources submits only its Dockerfile.
        assert peer.json("POST", "/images") == {"dockerfile": case.dockerfile}
        assert peer.bodies("POST", "/build-contexts") == []
        return
    write_builder_context(builder, destination)
    if case.sha256:
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == case.sha256
    # Then archive order, contents and relative links match the shared contract.
    with tarfile.open(destination) as archive:
        assert [entry.name + ("/" if entry.isdir() else "") for entry in archive] == case.entries
        for name, content in {"Dockerfile": case.dockerfile, **case.contents}.items():
            stream = archive.extractfile(name)
            assert stream is not None
            with stream:
                assert stream.read() == content.encode()
        for name, target in case.symlinks.items():
            entry = archive.getmember(".daytona-context/1/" + Path(name).name)
            assert entry.issym() and entry.linkname == target


@pytest.mark.parametrize("case", CASES.refused, ids=lambda case: case.name)
def test_shared_context_is_refused_at_builder_time(case: ContextCase, tmp_path: Path) -> None:
    # Given an invalid local-source declaration.
    # When its builder method is called.
    with pytest.raises(BadRequestError) as raised:
        make_builder(case, tmp_path)
    # Then the builder refuses it before archiving.
    assert case.error.replace("<root>", str(tmp_path)) in str(raised.value)


@pytest.mark.parametrize("case", CASES.buildRefused, ids=lambda case: case.name)
def test_shared_context_is_refused_at_archive_time(case: ContextCase, tmp_path: Path) -> None:
    # Given a valid directory declaration containing an unsafe link.
    builder = make_builder(case, tmp_path)
    # When its context is archived.
    with pytest.raises(BadRequestError) as raised:
        write_builder_context(builder, tmp_path / "context.tar")
    # Then the unsafe path is named.
    assert str(raised.value) == case.error.replace("<root>", str(tmp_path))


def test_sources_are_immutable_absolute_and_read_at_build_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a relative and a home-relative spelling of a local file.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "input.txt"
    source.write_text("before")
    base = ImageBuilder.from_registry("alpine")
    first = base.add_local_file("input.txt", "/first")
    second = first.add_local_file("~/input.txt", "/second")
    source.write_text("after")
    # When the newer builder is archived after the source changes.
    destination = tmp_path / "context.tar"
    write_builder_context(second, destination)
    # Then earlier builders stay unchanged and both sources use current bytes.
    assert base._local_sources == ()
    assert len(first._local_sources) == 1 and len(second._local_sources) == 2
    assert all(item.path == source and item.kind == "file" for item in second._local_sources)
    with tarfile.open(destination) as archive:
        for number in (1, 2):
            stream = archive.extractfile(f".daytona-context/{number}/input.txt")
            assert stream is not None
            with stream:
                assert stream.read() == b"after"


def test_archive_is_deterministic_and_keeps_permissions(tmp_path: Path) -> None:
    # Given an executable local file.
    source = tmp_path / "script"
    source.write_text("echo hello\n")
    source.chmod(0o751)
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/script")
    first, second = tmp_path / "first.tar", tmp_path / "second.tar"
    write_builder_context(builder, first)
    os.utime(source, (12345, 98765))
    # When the same bytes are archived with a different source timestamp.
    write_builder_context(builder, second)
    # Then all bytes stay stable and tar metadata is normalized except permissions.
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(second) as archive:
        assert archive.getmember(".daytona-context/1/script").mode == 0o751
        assert all(
            (item.mtime, item.uid, item.gid, item.uname, item.gname) == (0, 0, 0, "", "") for item in archive
        )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires FIFO support")
def test_fifo_in_local_directory_is_refused(tmp_path: Path) -> None:
    # Given a directory with a special file that must never be read.
    root = tmp_path / "source"
    root.mkdir()
    os.mkfifo(root / "pipe")
    builder = ImageBuilder.from_registry("alpine").add_local_dir(root, "/app")
    # When the directory is archived.
    with pytest.raises(BadRequestError, match="pipe"):
        write_builder_context(builder, tmp_path / "context.tar")
    # Then the FIFO was refused rather than opened (which would block).


def test_archive_succeeds_when_tree_is_1200_directories_deep(tmp_path: Path) -> None:
    # Given a tree deeper than Python's call stack, but below PATH_MAX.
    root = tmp_path / "source"
    root.mkdir()
    directories = [root]
    for _ in range(1200):
        child = directories[-1] / "d"
        child.mkdir()
        directories.append(child)
    leaf = directories[-1] / "f"
    leaf.write_bytes(b"deep")
    destination = tmp_path / "context.tar"
    try:
        # When the builder archives the real tree.
        write_builder_context(ImageBuilder.from_registry("alpine").add_local_dir(root, "/app"), destination)
        # Then its deepest file survives traversal and PAX serialization.
        with tarfile.open(destination) as archive:
            stream = archive.extractfile(".daytona-context/1/" + "d/" * 1200 + "f")
            assert stream is not None
            with stream:
                assert stream.read() == b"deep"
    finally:
        # Python's recursive tmpdir cleanup cannot remove this tree either.
        leaf.unlink()
        for directory in reversed(directories):
            directory.rmdir()


def test_symlinked_sources_are_copied_as_their_targets(tmp_path: Path) -> None:
    # Given a requirements file and a project directory that are both symlinks.
    (tmp_path / "real-requirements.txt").write_text("numpy\n")
    (tmp_path / "requirements.txt").symlink_to("real-requirements.txt")
    (tmp_path / "real-project").mkdir()
    (tmp_path / "real-project" / "app.py").write_text("print(1)\n")
    (tmp_path / "project").symlink_to("real-project")
    builder = (
        ImageBuilder.from_registry("alpine")
        .pip_install_from_requirements(tmp_path / "requirements.txt")
        .add_local_dir(tmp_path / "project", "/app")
    )
    # When the builder is archived.
    destination = tmp_path / "context.tar"
    write_builder_context(builder, destination)
    # Then both hold their targets' contents: a link would dangle under .daytona-context/<n>/.
    with tarfile.open(destination) as archive:
        requirements = archive.getmember(".daytona-context/1/requirements.txt")
        assert requirements.isfile()
        extracted = archive.extractfile(requirements)
        assert extracted is not None and extracted.read() == b"numpy\n"
        assert archive.getmember(".daytona-context/2/").isdir()
        assert archive.getmember(".daytona-context/2/app.py").isfile()


@pytest.mark.parametrize(
    "dockerfile",
    [
        "# escape=\nFROM alpine",
        "# escape=\n# escape=`\nFROM alpine",
        "# escape=\\\r\nFROM alpine",
        "# ordinary comment\n# escape=`\nFROM alpine",
        "# syntax=docker/dockerfile:1\n\n# escape=`\nFROM alpine",
        "# unsupported=value\n# escape=`\nFROM alpine",
        "FROM alpine\n# escape=`",
    ],
)
def test_context_is_accepted_when_default_escape_or_directive_scanning_stops(
    tmp_path: Path, dockerfile: str
) -> None:
    # Given a default escape or an escape-like comment outside the directive block.
    source = tmp_path / "input"
    source.write_bytes(b"content")
    builder = ImageBuilder.from_dockerfile(dockerfile).add_local_file(source, "/out/")
    destination = tmp_path / "context.tar"
    # When archived, then the file is still copied with its original contents.
    write_builder_context(builder, destination)
    with tarfile.open(destination) as archive:
        stream = archive.extractfile(".daytona-context/1/input")
        assert stream is not None
        with stream:
            assert stream.read() == b"content"


_ESCAPE_CASES = json.loads((Path(__file__).parent / "fixtures/dockerfile-escape.json").read_text("utf-8"))[
    "cases"
]


@pytest.mark.parametrize("case", _ESCAPE_CASES, ids=lambda case: repr(case["dockerfile"]))
def test_the_escape_directive_is_read_as_buildkit_reads_it(case: dict[str, str]) -> None:
    # The expectations come from BuildKit's own parser (shared with TypeScript).
    assert dockerfile_escape(case["dockerfile"]) == case["escape"]


_CONTINUATION_CASES = json.loads(
    (Path(__file__).parent / "fixtures/dockerfile-continuation.json").read_text("utf-8")
)["cases"]


@pytest.mark.parametrize("case", _CONTINUATION_CASES, ids=lambda case: repr(case["base"]))
def test_a_continued_base_is_read_as_buildkit_reads_it(case: dict[str, object]) -> None:
    # Expectations from BuildKit's own parser (shared with TypeScript).
    assert ends_with_continuation(str(case["base"])) is case["joins"]
