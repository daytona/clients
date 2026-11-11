# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Archive-time source changes and native cancellation boundaries."""

import asyncio
import errno
import json
import os
import sys
import tarfile
from collections.abc import Iterable
from pathlib import Path
from threading import Event

import pytest

from daytona.v2 import (
    BadRequestError,
    ImageBuilder,
    TransferCancelledError,
    _dockerignore,
    _image_archive,
    _image_context,
)


@pytest.mark.parametrize("directory", [False, True], ids=["file-to-dir", "dir-to-file"])
def test_source_kind_is_checked_when_archiving(tmp_path: Path, directory: bool) -> None:
    # Given a declared source subsequently replaced with the opposite kind.
    source = tmp_path / 'source"name'
    base = ImageBuilder.from_registry("alpine")
    if directory:
        source.mkdir()
        builder = base.add_local_dir(source, "/app")
        source.rmdir()
        source.write_bytes(b"replacement")
        kind = "directory"
    else:
        source.write_bytes(b"original")
        builder = base.add_local_file(source, "/app")
        source.unlink()
        source.mkdir()
        kind = "regular file"
    # When archived, then the declared kind is refused with the shared error.
    with pytest.raises(BadRequestError) as raised:
        _image_context.write_builder_context(builder, tmp_path / "context.tar")
    assert str(raised.value) == f"local source is not a {kind}: {json.dumps(str(source))}"


@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_source_is_missing_when_replaced_by_symlink_loop(tmp_path: Path, directory: bool) -> None:
    # Given a declared source replaced by a self-referential symlink.
    source = tmp_path / 'source"é'
    base = ImageBuilder.from_registry("alpine")
    if directory:
        source.mkdir()
        builder = base.add_local_dir(source, "/app")
        source.rmdir()
    else:
        source.write_bytes(b"content")
        builder = base.add_local_file(source, "/app")
        source.unlink()
    source.symlink_to(source.name)
    # When archived, then the loop is a typed missing-source error on every Python version.
    with pytest.raises(BadRequestError) as raised:
        _image_context.write_builder_context(builder, tmp_path / "context.tar")
    assert str(raised.value) == f"local source does not exist: {json.dumps(str(source), ensure_ascii=False)}"


@pytest.mark.parametrize(
    "code", [errno.EACCES, errno.EPERM, errno.ELOOP, errno.ENOENT, errno.ENXIO, errno.ENOTDIR, errno.EIO]
)
def test_open_error_is_classified_when_archiving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    # Given an OS open failure after the source has been collected.
    source = tmp_path / "input"
    source.write_bytes(b"content")
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/input")

    def refuse_open(path: str | os.PathLike[str], flags: int) -> int:
        raise OSError(code, "test open failure", path)

    monkeypatch.setattr(os, "open", refuse_open)
    # When archived, then known failures are typed and unrelated I/O propagates.
    if code == errno.EIO:
        with pytest.raises(OSError) as raised_io:
            _image_context.write_builder_context(builder, tmp_path / "context.tar")
        assert raised_io.value.errno == errno.EIO
    else:
        with pytest.raises(BadRequestError) as raised:
            _image_context.write_builder_context(builder, tmp_path / "context.tar")
        prefix = (
            "build context file is not readable"
            if code in (errno.EACCES, errno.EPERM)
            else "build context changed"
        )
        assert str(raised.value) == f"{prefix}: {json.dumps(str(source))}"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode-000 files")
def test_unreadable_file_is_named_when_archiving(tmp_path: Path) -> None:
    # Given an unreadable file in a declared directory.
    source = tmp_path / "source"
    source.mkdir()
    secret = source / "secret"
    secret.write_bytes(b"private")
    builder = ImageBuilder.from_registry("alpine").add_local_dir(source, "/app")
    secret.chmod(0)
    try:
        # When archived, then the error identifies permissions, not a race.
        with pytest.raises(BadRequestError) as raised:
            _image_context.write_builder_context(builder, tmp_path / "context.tar")
        assert str(raised.value) == f"build context file is not readable: {json.dumps(str(secret))}"
    finally:
        secret.chmod(0o600)


@pytest.mark.anyio
@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.timeout requires Python 3.11")
@pytest.mark.parametrize("directory", [False, True], ids=["builder", "directory"])
async def test_writer_finishes_before_cleanup_when_native_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    # Given a writer paused at its first write until native timeout cancellation.
    source = tmp_path / "source"
    source.mkdir()
    (source / "Dockerfile").write_bytes(b"FROM alpine\n")
    (source / "input").write_bytes(b"x" * 32768)
    builder = ImageBuilder.from_registry("alpine").add_local_dir(source, "/app")
    finished = Event()
    loop = asyncio.get_running_loop()
    original = _image_archive._CappedFile.write
    original_archive = _image_context._write_archive
    timeout = asyncio.timeout(None)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temporary))

    def paused_write(writer: _image_archive._CappedFile, data: object) -> int:
        assert writer._cancel is not None
        if not writer._cancel.is_set():
            loop.call_soon_threadsafe(timeout.reschedule, loop.time())
        # Cancellation must signal the worker before waiting for it.
        assert writer._cancel.wait(5)
        return original(writer, data)

    def record_finished(
        entries: Iterable[_image_archive._Entry], destination: Path, cancel: Event | None = None
    ) -> None:
        try:
            original_archive(entries, destination, cancel)
        finally:
            finished.set()

    monkeypatch.setattr(_image_archive._CappedFile, "write", paused_write)
    monkeypatch.setattr(_image_context, "_write_archive", record_finished)
    # When asyncio.timeout interrupts archive preparation mid-write.
    with pytest.raises(TimeoutError):
        async with (
            timeout,
            _image_context.prepared_context(source if directory else builder, "Dockerfile"),
        ):
            pytest.fail("cancelled archive must not be uploaded")
    # Then no archive work or temporary build directory outlives the call.
    assert finished.is_set()
    assert list(temporary.glob("daytona-build-*")) == []


@pytest.mark.parametrize("after_open", [False, True], ids=["before-open", "after-open"])
def test_appended_file_uses_opened_size_when_archiving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_open: bool
) -> None:
    # Given in-place growth either between walk/open or immediately after fstat.
    source = tmp_path / "input"
    source.write_bytes(b"before\n")
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/input")
    original_add = _image_archive._add_path
    original_stat = os.fstat

    def append() -> None:
        with source.open("ab") as handle:
            handle.write(b"appended\n")

    def add(archive: tarfile.TarFile, info: tarfile.TarInfo, entry: _image_archive._Entry) -> None:
        append()
        original_add(archive, info, entry)

    def fstat(descriptor: int) -> os.stat_result:
        result = original_stat(descriptor)
        append()
        return result

    if after_open:
        monkeypatch.setattr(os, "fstat", fstat)
    else:
        monkeypatch.setattr(_image_archive, "_add_path", add)
    destination = tmp_path / "context.tar"
    # When archived, then only the opened size determines the copied bytes.
    _image_context.write_builder_context(builder, destination)
    with tarfile.open(destination) as archive:
        stream = archive.extractfile(".daytona-context/1/input")
        assert stream is not None
        with stream:
            assert stream.read() == (b"before\n" if after_open else b"before\nappended\n")


def test_short_read_is_refused_when_file_shrinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a file truncated after fstat reported its opened size.
    source = tmp_path / "input"
    source.write_bytes(b"before\n")
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/input")
    original_stat = os.fstat

    def shrink(descriptor: int) -> os.stat_result:
        result = original_stat(descriptor)
        source.write_bytes(b"")
        return result

    monkeypatch.setattr(os, "fstat", shrink)
    # When archived, then the error is the shared race refusal, not raw tarfile I/O.
    with pytest.raises(BadRequestError) as raised:
        _image_context.write_builder_context(builder, tmp_path / "context.tar")
    assert str(raised.value) == f"build context changed: {json.dumps(str(source))}"


def test_capped_writer_closes_when_initial_open_failed(tmp_path: Path) -> None:
    # Given an uninitialized IOBase whose destination parent does not exist.
    writer = _image_archive._CappedFile.__new__(_image_archive._CappedFile)
    with pytest.raises(FileNotFoundError):
        writer.__init__(tmp_path / "missing/context.tar")
    # When IOBase finalizes it, then close tolerates the partially initialized state.
    writer.close()
    assert writer.closed


@pytest.mark.parametrize("directory", [False, True], ids=["builder", "directory"])
def test_cancel_stops_walk_when_collecting_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    # Given a cancellation delivered during collection, before writing a tar.
    source = tmp_path / "source"
    source.mkdir()
    (source / "Dockerfile").write_bytes(b"FROM alpine\n")
    (source / "a").mkdir()
    (source / "a/unvisited").write_bytes(b"x")
    builder = ImageBuilder.from_registry("alpine").add_local_dir(source, "/app")
    cancel = Event()
    original = _dockerignore.Filter.keep

    def keep(
        filter: _dockerignore.Filter, rel: str, parent: tuple[bool, ...] = ()
    ) -> tuple[bool, bool, tuple[bool, ...]]:
        assert not cancel.is_set(), "walk continued after cancellation"
        result = original(filter, rel, parent)
        cancel.set()
        return result

    monkeypatch.setattr(_dockerignore.Filter, "keep", keep)
    # When collecting either kind of context, then traversal stops cooperatively.
    with pytest.raises(TransferCancelledError):
        if directory:
            _image_context.write_directory_context(source, "Dockerfile", tmp_path / "out.tar", cancel=cancel)
        else:
            _image_context.write_builder_context(builder, tmp_path / "out.tar", cancel=cancel)


@pytest.mark.parametrize("declaration", [False, True], ids=["walk", "local-source"])
@pytest.mark.parametrize("ignored", [False, True], ids=["kept", "ignored"])
def test_non_utf8_filename_is_refused_when_preparing_context(
    tmp_path: Path, declaration: bool, ignored: bool
) -> None:
    # Given a filesystem name that cannot be represented by a UTF-8 archive.
    raw = os.fsencode(tmp_path) + b"/bad\xff"
    try:
        with open(raw, "wb") as stream:
            stream.write(b"x")
    except (OSError, UnicodeError):
        pytest.skip("filesystem refuses non-UTF-8 filenames")
    (tmp_path / "Dockerfile").write_bytes(b"FROM alpine\n")
    if ignored:
        (tmp_path / ".dockerignore").write_bytes(b"bad*\n")
    # When declared or walked (even if ignored), then the SDK names the bad path.
    with pytest.raises(BadRequestError) as raised:
        if declaration:
            ImageBuilder.from_registry("alpine").add_local_file(os.fsdecode(raw), "/app/")
        else:
            _image_context.write_directory_context(tmp_path, "Dockerfile", tmp_path.parent / "out.tar")
    assert str(raised.value) == "build context path is not valid UTF-8: " + json.dumps(
        raw.decode("utf-8", errors="replace"), ensure_ascii=False
    )
