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
from typing import TypeVar

import anyio
from typing_extensions import assert_never

from ._context_upload import BuildContext
from ._dockerignore import Filter, Matcher, read_patterns, walk_archive_entries
from ._image_archive import _Entry, _write_archive
from ._image_source import expand_home, utf8_path
from ._image_text import dockerfile_escape
from .errors import TransferCancelledError, create_error
from .image import ImageBuilder

_T = TypeVar("_T")


def _regular_file(root: Path, relative: str) -> Path:
    filename = root / relative
    quoted = json.dumps(str(filename), ensure_ascii=False)
    try:
        filename.lstat()
    except PermissionError as error:
        # A parent directory that cannot be searched.
        raise create_error(
            f"build context file is not readable: {quoted}", 400, "BAD_REQUEST", "SDK"
        ) from error
    except OSError:
        pass  # Missing or a loop: resolve() below raises what the caller maps.
    else:
        # Only an existing target can be unreadable; a dangling link or loop is
        # reported by resolve() below.
        if os.path.exists(filename) and not os.access(filename, os.R_OK):
            raise create_error(f"build context file is not readable: {quoted}", 400, "BAD_REQUEST", "SDK")
    resolved = filename.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise create_error(f"build context file leaves its local source: {quoted}", 400, "BAD_REQUEST", "SDK")
    if not resolved.is_file():
        raise create_error(f"build context path is not a regular file: {quoted}", 400, "BAD_REQUEST", "SDK")
    return filename


def _directory_filter(root: Path, dockerfile: str | None) -> Filter:
    utf8_path(str(root))
    # Listing is probed up front: an unreadable root is a typed refusal, as an
    # unreadable directory found by the walk is.
    if not os.access(root, os.R_OK | os.X_OK):
        quoted = json.dumps(str(root), ensure_ascii=False)
        raise create_error(f"build context directory is not readable: {quoted}", 400, "BAD_REQUEST", "SDK")
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
        except (OSError, RuntimeError) as error:
            # A link loop: Path.resolve raises RuntimeError on Python 3.10-3.12.
            if isinstance(error, OSError) and error.errno != errno.ELOOP:
                raise
            quoted = json.dumps(str(root / name), ensure_ascii=False)
            raise create_error(
                f"build context path is not a regular file: {quoted}", 400, "BAD_REQUEST", "SDK"
            ) from error
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
    _write_archive(collect_builder_entries(builder, cancel), destination, cancel)


def collect_builder_entries(builder: ImageBuilder, cancel: Event | None = None) -> list[_Entry]:
    """The archive entries of a builder: its Dockerfile and positional sources."""
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
        # A path that was not a link when it was added and is one now has been
        # redirected: refused, so a swap between declaration and build cannot
        # pull in another tree.
        if not source.link and os.path.islink(source.path):
            raise create_error(
                f"local source changed since it was added: {json.dumps(str(source.path), ensure_ascii=False)}",
                400,
                "BAD_REQUEST",
                "SDK",
            )
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
    return entries


def write_directory_context(
    root: Path, dockerfile: str, destination: Path, *, cancel: Event | None = None
) -> None:
    """Archive a directory with Dockerfile-specific ignore-file precedence."""
    _write_archive(collect_directory_entries(root, dockerfile, cancel), destination, cancel)


def collect_directory_entries(root: Path, dockerfile: str, cancel: Event | None = None) -> list[_Entry]:
    """The archive entries of a directory context."""
    absolute = Path(os.path.realpath(expand_home(os.fspath(root))))
    return [
        _Entry(relative, path, relative)
        for relative, path in _directory_entries(absolute, dockerfile, cancel)
    ]


async def _write_in_thread(writer: Callable[[], _T], cancel: Event) -> _T:
    """Join the owned worker even when native asyncio cancellation interrupts us."""
    worker = asyncio.create_task(anyio.to_thread.run_sync(writer))
    try:
        return await asyncio.shield(worker)
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
    # A blank path would become `.`: the working directory must never be
    # archived by accident (TypeScript refuses it as a missing tar file).
    if (
        isinstance(context, (str, os.PathLike))
        and not isinstance(context, Path)
        and not os.fspath(context).strip()
    ):
        raise create_error("build context path must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    expanded = Path(expand_home(os.fspath(context))) if isinstance(context, (str, os.PathLike)) else context
    if isinstance(expanded, Path):
        utf8_path(str(expanded))
    # Entries are collected before the temporary directory exists: a temporary
    # root inside the source tree must not be archived (TypeScript orders it
    # the same way).
    if isinstance(expanded, ImageBuilder):
        cancel = Event()
        entries = await _write_in_thread(partial(collect_builder_entries, expanded, cancel), cancel)
        with TemporaryDirectory(prefix="daytona-build-") as directory:
            destination = Path(directory) / "context.tar"
            await _write_in_thread(partial(_write_archive, entries, destination, cancel), cancel)
            try:
                yield destination
            finally:
                destination.unlink(missing_ok=True)
    # os.path.isdir swallows every OSError on all supported Pythons (Path.is_dir
    # raises EACCES/ENAMETOOLONG before 3.14); the tar-path upload then refuses
    # a path it cannot stat with a BadRequestError.
    elif isinstance(expanded, Path) and os.path.isdir(expanded):
        cancel = Event()
        entries = await _write_in_thread(
            partial(collect_directory_entries, expanded, dockerfile, cancel), cancel
        )
        with TemporaryDirectory(prefix="daytona-build-") as directory:
            destination = Path(directory) / "context.tar"
            await _write_in_thread(partial(_write_archive, entries, destination, cancel), cancel)
            try:
                yield destination
            finally:
                destination.unlink(missing_ok=True)
    else:
        yield expanded
