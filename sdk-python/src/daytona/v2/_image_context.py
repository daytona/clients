# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic, filesystem-backed image contexts (ADR 0006)."""

from __future__ import annotations

import asyncio  # Native Task.cancel() must not abandon the AnyIO writer thread.
import errno
import json
import ntpath
import os
import posixpath
import stat
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

import anyio
from typing_extensions import assert_never

from ._context_upload import BuildContext
from ._dockerignore import Filter, Matcher, read_patterns, walk_archive_entries
from ._image_archive import _Entry, _write_archive
from ._image_source import expand_home, utf8_path
from ._image_text import dockerfile_escape
from .errors import TransferCancelledError, create_error
from .image import ImageBuilder


def _regular_file(root: Path, relative: str) -> Path:
    filename = root / relative
    resolved = filename.resolve(strict=True)
    quoted = json.dumps(str(filename), ensure_ascii=False)
    if not resolved.is_relative_to(root):
        raise create_error(f"build context file leaves its local source: {quoted}", 400, "BAD_REQUEST", "SDK")
    if not resolved.is_file():
        raise create_error(f"build context path is not a regular file: {quoted}", 400, "BAD_REQUEST", "SDK")
    return filename


def _directory_filter(root: Path, dockerfile: str | None) -> Filter:
    utf8_path(str(root))
    candidates = [".dockerignore"]
    always: tuple[str, ...] = ()
    if dockerfile is not None:
        clean = os.path.normpath(dockerfile)
        if (
            "\0" in dockerfile
            or dockerfile.startswith(("/", "\\"))
            or ntpath.isabs(dockerfile)
            or clean in (".", "..")
            or clean.startswith(".." + os.sep)
        ):
            raise create_error(
                "dockerfile must be a relative path inside the build context", 400, "BAD_REQUEST", "SDK"
            )
        try:
            _regular_file(root, clean)
        except (OSError, RuntimeError) as error:
            # Path.resolve reports link loops as RuntimeError on Python 3.10-3.12.
            if isinstance(error, OSError) and error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                raise
            raise create_error(
                f"dockerfile {json.dumps(dockerfile, ensure_ascii=False)} does not exist in the build context",
                400,
                "BAD_REQUEST",
                "SDK",
            ) from error
        clean = Path(clean).as_posix()
        candidates.insert(0, clean + ".dockerignore")
        always = (".dockerignore", clean)
    for name in candidates:
        try:
            text = _regular_file(root, name).read_bytes().decode("utf-8", errors="replace")
        except FileNotFoundError:
            continue
        return Filter(Matcher(read_patterns(text)), (*always, name) if dockerfile is not None else ())
    return Filter(Matcher(()))


def _directory_entries(
    root: Path, dockerfile: str | None, cancel: Event | None
) -> Iterable[tuple[str, Path]]:
    for relative, path in walk_archive_entries(root, _directory_filter(root, dockerfile), cancel):
        # A builder's ignore file controls the copy; it is not itself copied.
        if dockerfile is None and relative.rstrip("/") == ".dockerignore":
            continue
        yield relative, path


def write_builder_context(builder: ImageBuilder, destination: Path, *, cancel: Event | None = None) -> None:
    """Write the builder's Dockerfile and positional sources to a PAX tar."""
    dockerfile = builder.to_dockerfile()
    # Appended COPY basenames are escaped for the default Dockerfile lexer.
    if builder._local_sources and dockerfile_escape(dockerfile) != "\\":
        raise create_error(
            "local files need the default Dockerfile escape character", 400, "BAD_REQUEST", "SDK"
        )
    entries = [_Entry("Dockerfile", content=dockerfile.encode("utf-8"))]
    entries.append(_Entry(".daytona-context/"))
    for source in builder._local_sources:
        if cancel is not None and cancel.is_set():
            raise TransferCancelledError()
        # The source itself is followed, as `docker build` follows a named
        # path: a symlinked requirements file is copied as its content, not as
        # a link that would dangle under `.daytona-context/<n>/`. Links inside
        # an added directory stay links (_add_path).
        try:
            real = source.path.resolve(strict=True)
            info = os.stat(real)
        except (OSError, RuntimeError) as error:
            # Path.resolve reports link loops as RuntimeError on Python 3.10-3.12.
            if isinstance(error, OSError) and error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                raise
            raise create_error(
                f"local source does not exist: {json.dumps(str(source.path), ensure_ascii=False)}",
                400,
                "BAD_REQUEST",
                "SDK",
            ) from error
        match source.kind:
            case "file":
                if not stat.S_ISREG(info.st_mode):
                    raise create_error(
                        f"local source is not a regular file: {json.dumps(str(source.path), ensure_ascii=False)}",
                        400,
                        "BAD_REQUEST",
                        "SDK",
                    )
                parent = posixpath.dirname(source.archive_path)
                entries.append(_Entry(parent + "/"))
                entries.append(_Entry(source.archive_path, real, source.path.name))
            case "dir":
                if not stat.S_ISDIR(info.st_mode):
                    raise create_error(
                        f"local source is not a directory: {json.dumps(str(source.path), ensure_ascii=False)}",
                        400,
                        "BAD_REQUEST",
                        "SDK",
                    )
                entries.append(_Entry(source.archive_path + "/", real))
                entries.extend(
                    _Entry(source.archive_path + "/" + relative, path, relative)
                    for relative, path in _directory_entries(real, None, cancel)
                )
            case unreachable:
                assert_never(unreachable)
    _write_archive(entries, destination, cancel)


def write_directory_context(
    root: Path, dockerfile: str, destination: Path, *, cancel: Event | None = None
) -> None:
    """Archive a directory with Dockerfile-specific ignore-file precedence."""
    absolute = Path(os.path.realpath(expand_home(os.fspath(root))))
    entries = [
        _Entry(relative, path, relative)
        for relative, path in _directory_entries(absolute, dockerfile, cancel)
    ]
    _write_archive(entries, destination, cancel)


async def _write_in_thread(writer: Callable[[], None], cancel: Event) -> None:
    """Join the owned worker even when native asyncio cancellation interrupts us."""
    worker = asyncio.create_task(anyio.to_thread.run_sync(writer))
    try:
        await asyncio.shield(worker)
    except BaseException:  # Cleanup must join on cancellation and on writer failure, then re-raise.
        cancel.set()
        with anyio.CancelScope(shield=True):
            # Retrieve failures without replacing the original exception.
            await asyncio.shield(asyncio.gather(worker, return_exceptions=True))
        raise


@asynccontextmanager
async def prepared_context(
    context: BuildContext | ImageBuilder, dockerfile: str
) -> AsyncIterator[BuildContext]:
    """Keep a generated tar alive until upload/build returns, including failure."""
    if not isinstance(dockerfile, str) or not dockerfile.strip():
        raise create_error("dockerfile must be a non-empty path", 400, "BAD_REQUEST", "SDK")
    expanded = Path(expand_home(os.fspath(context))) if isinstance(context, (str, os.PathLike)) else context
    if isinstance(expanded, Path):
        utf8_path(str(expanded))
    if isinstance(expanded, ImageBuilder):
        with TemporaryDirectory(prefix="daytona-build-") as directory:
            destination = Path(directory) / "context.tar"
            cancel = Event()
            await _write_in_thread(
                partial(write_builder_context, expanded, destination, cancel=cancel), cancel
            )
            try:
                yield destination
            finally:
                destination.unlink(missing_ok=True)
    elif isinstance(expanded, Path) and expanded.is_dir():
        with TemporaryDirectory(prefix="daytona-build-") as directory:
            destination = Path(directory) / "context.tar"
            cancel = Event()
            await _write_in_thread(
                partial(write_directory_context, expanded, dockerfile, destination, cancel=cancel), cancel
            )
            try:
                yield destination
            finally:
                destination.unlink(missing_ok=True)
    else:
        yield expanded
