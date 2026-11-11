# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Archive-format and source-boundary cases beyond the shared fixtures."""

import hashlib
import json
import os
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from test_build_context_upload import build, storage_peer
from test_image_context_upload import uploaded_bytes

from daytona.v2 import BadRequestError, ImageBuilder, _context_upload
from daytona.v2._image_context import prepared_context, write_builder_context, write_directory_context


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryCase:
    name: str
    files: dict[str, str]
    symlinks: dict[str, str] = field(default_factory=dict)
    fifos: tuple[str, ...] = ()
    ignoreLineLength: int = 0
    contextPath: str = "."
    dockerfile: str = "Dockerfile"
    entries: list[str] = field(default_factory=list)
    error: str = ""
    sha256: str = ""


def write_tree(root: Path, case: DirectoryCase) -> None:
    for name, text in case.files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith("/"):
            path.mkdir(exist_ok=True)
        else:
            path.write_text(text, encoding="utf-8")
            path.chmod(0o644)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    for name, target in case.symlinks.items():
        (root / name).symlink_to(target)
    if case.fifos and not hasattr(os, "mkfifo"):
        pytest.skip("requires filesystem FIFO support")
    for name in case.fifos:
        os.mkfifo(root / name)
    if case.ignoreLineLength:
        (root / ".dockerignore").write_text("a" * case.ignoreLineLength, encoding="utf-8")


FIXTURE = Path(__file__).parent / "fixtures"
DIRECTORIES = TypeAdapter(dict[str, list[DirectoryCase]]).validate_json(
    (FIXTURE / "image-builder-directories.json").read_bytes()
)
ARCHIVES = TypeAdapter(dict[str, list[DirectoryCase]]).validate_json(
    (FIXTURE / "image-builder-archives.json").read_bytes()
)


@pytest.mark.anyio
@pytest.mark.parametrize("case", DIRECTORIES["directoryCases"], ids=lambda case: case.name)
async def test_directory_context_entries_when_shared_tree(case: DirectoryCase, tmp_path: Path) -> None:
    # Given a context tree, including symlinked roots.
    write_tree(tmp_path, case)
    # When the public build preparation chooses the directory branch.
    async with prepared_context(tmp_path / case.contextPath, case.dockerfile) as context:
        assert isinstance(context, Path)
        with tarfile.open(context) as archive:
            # Then it contains exactly the shared entries.
            assert [entry.name + ("/" if entry.isdir() else "") for entry in archive] == case.entries


@pytest.mark.parametrize("case", DIRECTORIES["directoryRefused"], ids=lambda case: case.name)
def test_directory_context_refused_when_invalid(case: DirectoryCase, tmp_path: Path) -> None:
    # Given invalid directory build inputs shared by both SDKs.
    write_tree(tmp_path, case)
    # When the directory is archived, then the SDK reports the same local error.
    with pytest.raises(BadRequestError) as raised:
        write_directory_context(tmp_path / case.contextPath, case.dockerfile, tmp_path / "context.tar")
    assert str(raised.value) == case.error.replace("<root>", str(tmp_path))


@pytest.mark.parametrize("case", ARCHIVES["cases"], ids=lambda case: case.name)
def test_archive_digest_when_shared_tree(case: DirectoryCase, tmp_path: Path) -> None:
    # Given explicit file/directory modes, independent of the process umask.
    write_tree(tmp_path, case)
    destination = tmp_path.parent / (tmp_path.name + ".tar")
    # When archived with normalized metadata.
    write_directory_context(tmp_path, "Dockerfile", destination)
    # Then both SDKs produce the same CPython PAX_FORMAT stream bytes.
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == case.sha256


def test_nested_dockerfile_and_ignore_are_kept_by_literal_path(tmp_path: Path) -> None:
    # Given a nested Dockerfile whose name contains pattern syntax.
    root = tmp_path / "project"
    (root / "build").mkdir(parents=True)
    dockerfile = "build/Docker[1]file"
    (root / dockerfile).write_text("FROM alpine\n")
    (root / (dockerfile + ".dockerignore")).write_text("**\n!keep\n")
    (root / ".dockerignore").write_text("keep\n")
    (root / "keep").write_text("included")
    (root / "drop").write_text("excluded")
    destination = tmp_path / "context.tar"

    # When the directory is archived using the Dockerfile-specific ignore file.
    write_directory_context(root, dockerfile, destination)

    # Then required paths and their excluded parent survive as literal names.
    with tarfile.open(destination) as archive:
        assert archive.getnames() == [
            ".dockerignore",
            "build",
            dockerfile,
            dockerfile + ".dockerignore",
            "keep",
        ]


def test_long_paths_use_deterministic_pax_headers(tmp_path: Path) -> None:
    # Given a filename longer than the legacy tar name field.
    root = tmp_path / "source"
    root.mkdir()
    source = root / ("long-" * 30 + "file")
    source.write_bytes(b"long-name-content")
    builder = ImageBuilder.from_registry("alpine").add_local_dir(root, "/app")
    destination = tmp_path / "context.tar"

    # When the builder's context is written.
    write_builder_context(builder, destination)

    # Then PAX preserves the exact name and data instead of truncating either.
    name = ".daytona-context/1/" + source.name
    with tarfile.open(destination) as archive:
        member = archive.getmember(name)
        assert member.pax_headers == {"path": name}
        stream = archive.extractfile(member)
        assert stream is not None
        with stream:
            assert stream.read() == b"long-name-content"


def test_a_symlinked_directory_source_is_followed(tmp_path: Path) -> None:
    # Given a directory source that is a symlink to a directory elsewhere.
    target = tmp_path / "outside"
    target.mkdir()
    (target / "file").write_bytes(b"outside")
    link = tmp_path / "source"
    link.symlink_to(target, target_is_directory=True)
    builder = ImageBuilder.from_registry("alpine").add_local_dir(link, "/app")

    # When it is archived.
    destination = tmp_path / "context.tar"
    write_builder_context(builder, destination)

    # Then the named path is followed, as docker build follows its context.
    with tarfile.open(destination) as archive:
        assert archive.getmember(".daytona-context/1/").isdir()
        extracted = archive.extractfile(".daytona-context/1/file")
        assert extracted is not None and extracted.read() == b"outside"


def test_the_archive_stops_at_the_upload_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a limit smaller than the source.
    monkeypatch.setattr(_context_upload, "MAX_CONTEXT_BYTES", 64 * 1024)
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * (256 * 1024))
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/big.bin")
    destination = tmp_path / "context.tar"
    # When it is archived.
    with pytest.raises(BadRequestError, match="exceeds the 1 GiB limit"):
        write_builder_context(builder, destination)
    # Then writing stopped near the limit instead of copying the whole source.
    assert destination.stat().st_size <= 64 * 1024 + 10240


def test_a_file_swapped_for_a_link_while_archiving_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a directory whose file is replaced by a link to an outside file
    # between the walk's lstat and the open.
    (tmp_path / "outside").write_bytes(b"secret")
    root = tmp_path / "source"
    root.mkdir()
    victim = root / "f"
    victim.write_bytes(b"inside")
    builder = ImageBuilder.from_registry("alpine").add_local_dir(root, "/app")
    real_open = os.open

    def swapping_open(path: str | os.PathLike[str], flags: int) -> int:
        if Path(path) == victim:
            victim.unlink()
            victim.symlink_to(tmp_path / "outside")
        return real_open(path, flags)

    monkeypatch.setattr(os, "open", swapping_open)
    # When it is archived.
    with pytest.raises(BadRequestError, match="build context changed: " + json.dumps(str(victim))):
        write_builder_context(builder, tmp_path / "context.tar")


def test_parent_swap_is_refused_when_archive_reads_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given same-sized files inside/outside a walked source tree.
    from daytona.v2 import _image_archive

    root, outside = tmp_path / "source", tmp_path / "outside"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    victim = root / "sub/f"
    victim.write_bytes(b"inside")
    (outside / "f").write_bytes(b"secret")
    builder = ImageBuilder.from_registry("alpine").add_local_dir(root, "/app")
    original = _image_archive._add_path

    def swap(archive: tarfile.TarFile, info: tarfile.TarInfo, entry: _image_archive._Entry) -> None:
        if entry.path == victim:
            (root / "sub").rename(root / "old")
            (root / "sub").symlink_to(outside, target_is_directory=True)
        original(archive, info, entry)

    monkeypatch.setattr(_image_archive, "_add_path", swap)
    # When a parent is redirected after the walk, then walk-time identity wins.
    with pytest.raises(BadRequestError, match="build context changed:"):
        write_builder_context(builder, tmp_path / "context.tar")


def test_ignore_decoding_replaces_invalid_utf8_when_archiving(tmp_path: Path) -> None:
    # Given an ignore file with an invalid UTF-8 byte and a valid pattern.
    (tmp_path / "Dockerfile").write_bytes(b"FROM alpine\n")
    (tmp_path / ".dockerignore").write_bytes(b"\xff\ndrop\n")
    (tmp_path / "drop").write_bytes(b"excluded")
    destination = tmp_path.parent / (tmp_path.name + ".tar")
    # When archived, then decoding does not fail or lose the valid pattern.
    write_directory_context(tmp_path, "Dockerfile", destination)
    with tarfile.open(destination) as archive:
        assert archive.getnames() == [".dockerignore", "Dockerfile"]


@pytest.mark.anyio
@pytest.mark.parametrize("directory", [False, True], ids=["tar", "directory"])
@pytest.mark.parametrize("named_user", [False, True], ids=["home", "literal-tilde-user"])
async def test_context_home_rule_when_building_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, named_user: bool
) -> None:
    # Given a home-relative path or a literal ~root name in the working directory.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    parent = tmp_path / "~root" if named_user else tmp_path
    parent.mkdir(exist_ok=True)
    source = parent / "context"
    if directory:
        source.mkdir()
        (source / "Dockerfile").write_bytes(b"FROM alpine\n")
    else:
        source.write_bytes(b"opaque tar content")
    spelling = "~root/context" if named_user else "~/context"
    with storage_peer() as peer:
        # When the SDK builds from the path (directory detection or tar upload).
        await build(peer, spelling, False)
    # Then the selected path was read with the same rule in both branches.
    if directory:
        import io

        with tarfile.open(fileobj=io.BytesIO(uploaded_bytes(peer))) as archive:
            assert archive.getnames() == ["Dockerfile"]
    else:
        assert uploaded_bytes(peer) == b"opaque tar content"


def test_named_user_is_literal_when_builder_records_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an existing literal ~root directory, not the root user's home.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "~root").mkdir()
    (tmp_path / "~root/input").write_bytes(b"literal")
    # When a local file is declared, then the builder records the literal path.
    builder = ImageBuilder.from_registry("alpine").add_local_file("~root/input", "/input")
    assert builder._local_sources[0].path == tmp_path / "~root/input"


@pytest.mark.anyio
async def test_bare_home_is_expanded_when_preparing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a home directory containing a Dockerfile.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Dockerfile").write_bytes(b"FROM alpine\n")
    # When the context is bare ~, then its contents are archived.
    async with prepared_context("~", "Dockerfile") as context:
        assert isinstance(context, Path)
        with tarfile.open(context) as archive:
            assert archive.getnames() == ["Dockerfile"]


@pytest.mark.parametrize("prefix", ["# escape=`", "# syntax=docker/dockerfile:1\n# EsCaPe = `"])
def test_alternate_escape_is_refused_when_source_disappeared(tmp_path: Path, prefix: str) -> None:
    # Given a local source declared before it disappears from disk.
    source = tmp_path / "input"
    source.write_bytes(b"x")
    builder = ImageBuilder.from_dockerfile(prefix + "\nFROM alpine").add_local_file(source, "/app/")
    source.unlink()
    # When built, then directive refusal precedes any attempt to read the source.
    with pytest.raises(BadRequestError) as raised:
        write_builder_context(builder, tmp_path / "context.tar")
    assert str(raised.value) == "local files need the default Dockerfile escape character"


def test_a_symlink_target_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    # Given a link whose target bytes are not UTF-8 (Node would read them with
    # replacement characters, so both SDKs refuse instead of diverging).
    root = tmp_path / "src"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM alpine\n")
    try:
        os.symlink(b"\xff\xfeweird", os.fsencode(root / "badlink"))
    except OSError:
        pytest.skip("the filesystem refuses non-UTF-8 link targets")
    # When the directory is archived.
    with pytest.raises(BadRequestError, match="build context path is not valid UTF-8"):
        write_directory_context(root, "Dockerfile", tmp_path / "context.tar")


def test_the_first_refusal_is_in_walk_order_with_a_later_non_utf8_name(tmp_path: Path) -> None:
    # Given an escaping link in a/ and an undecodable name in z/.
    root = tmp_path / "order"
    (root / "a").mkdir(parents=True)
    (root / "z").mkdir()
    (root / "Dockerfile").write_text("FROM alpine\n")
    (root / "a" / "link").symlink_to("../../x")
    try:
        (Path(os.fsdecode(os.fsencode(root / "z") + b"/b\xff"))).write_text("x")
    except OSError:
        pytest.skip("the filesystem refuses non-UTF-8 names")
    # When archived, then the link (reached first) is the refusal, as in TypeScript.
    with pytest.raises(BadRequestError) as raised:
        write_directory_context(root, "Dockerfile", tmp_path / "context.tar")
    assert str(raised.value) == f'symbolic link leaves its local source: "{root / "a" / "link"}"'


def test_directory_entries_are_walked_in_utf8_byte_order(tmp_path: Path) -> None:
    # Given an undecodable name (0xFF) and a private-use name (U+E000).
    root = tmp_path / "bytes"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM alpine\n")
    os.mkfifo(root / "\ue000x")
    try:
        Path(os.fsdecode(os.fsencode(root) + b"/\xff")).write_text("x")
    except OSError:
        pytest.skip("the filesystem refuses non-UTF-8 names")
    # When archived, then U+E000 (0xEE...) sorts before 0xFF, as Go and
    # TypeScript sort: the FIFO is reached, and refused, first.
    with pytest.raises(BadRequestError, match="unsupported file type"):
        write_directory_context(root, "Dockerfile", tmp_path / "context.tar")


def test_pip_install_from_requirements_refuses_0x_pip_options(tmp_path: Path) -> None:
    # 0.x took pip options here; silently dropping an index URL would install
    # from the default index (TypeScript refuses the same call).
    requirements = tmp_path / "req.txt"
    requirements.write_text("numpy\n")
    with pytest.raises(TypeError):
        ImageBuilder.from_registry("alpine").pip_install_from_requirements(  # type: ignore[call-arg]  # the 0.x keyword is gone
            requirements, index_url="https://private.example/simple"
        )


_POSIX_NON_ROOT = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0


@pytest.mark.skipif(not _POSIX_NON_ROOT, reason="needs POSIX permissions enforced for the user")
def test_an_unreadable_context_root_or_ignore_file_is_a_typed_refusal(tmp_path: Path) -> None:
    # Given a context root that cannot be listed, and one whose .dockerignore
    # cannot be read.
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "Dockerfile").write_text("FROM alpine\n")
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "Dockerfile").write_text("FROM alpine\n")
    (ignored / ".dockerignore").write_text("x\n")
    locked.chmod(0)
    (ignored / ".dockerignore").chmod(0)
    try:
        # When archived, then both raise BadRequestError naming the path.
        with pytest.raises(BadRequestError, match="build context directory is not readable"):
            write_directory_context(locked, "Dockerfile", tmp_path / "a.tar")
        with pytest.raises(BadRequestError, match="build context file is not readable"):
            write_directory_context(ignored, "Dockerfile", tmp_path / "b.tar")
        with pytest.raises(BadRequestError, match="build context directory is not readable"):
            write_builder_context(
                ImageBuilder.from_registry("alpine").add_local_dir(tmp_path / "locked", "/app"),
                tmp_path / "c.tar",
            )
    finally:
        locked.chmod(0o755)
        (ignored / ".dockerignore").chmod(0o644)


@pytest.mark.anyio
@pytest.mark.skipif(not _POSIX_NON_ROOT, reason="needs POSIX permissions enforced for the user")
async def test_an_unreachable_tar_path_is_the_tar_upload_error(tmp_path: Path) -> None:
    # Given a tar path under a directory the user cannot search, and a link loop.
    parent = tmp_path / "noexec"
    parent.mkdir()
    (parent / "c.tar").write_bytes(b"x")
    parent.chmod(0o600)
    (tmp_path / "loop1").symlink_to(tmp_path / "loop2")
    (tmp_path / "loop2").symlink_to(tmp_path / "loop1")
    try:
        # When prepared, then the directory probe does not raise: the path goes
        # on to the tar upload, which owns the typed error.
        for path in (parent / "c.tar", tmp_path / "loop1", tmp_path / ("x" * 300)):
            async with prepared_context(path, "Dockerfile") as context:
                assert context == path
    finally:
        parent.chmod(0o755)


@pytest.mark.anyio
@pytest.mark.parametrize("context", ["", "  "])
async def test_a_blank_context_path_is_refused_not_the_working_directory(context: str) -> None:
    # A blank path would become `.`: it must never archive the working directory.
    with pytest.raises(BadRequestError, match="build context path must be a non-empty string"):
        async with prepared_context(context, "Dockerfile"):
            pass


@pytest.mark.anyio
async def test_a_blank_path_like_context_is_refused() -> None:
    # An os.PathLike returning "" would also become `.`; a pathlib.Path("") is
    # already `.` by pathlib's own rules and is left to mean that.
    class Blank:
        def __fspath__(self) -> str:
            return " "

    with pytest.raises(BadRequestError, match="build context path must be a non-empty string"):
        async with prepared_context(Blank(), "Dockerfile"):
            pass
