# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic PAX serialization with bounded, cooperative file writes."""

from __future__ import annotations

import errno
import io
import json
import os
import posixpath
import stat
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from . import _context_upload
from ._image_source import utf8_path
from .errors import TransferCancelledError, create_error


@dataclass(frozen=True, slots=True)
class _Entry:
    name: str
    path: Path | None = None
    # The relative source name bounds a symlink to its own source, not the tar.
    relative: str = ""
    content: bytes | None = None
    metadata: os.stat_result | None = None
    link_target: str = ""

    def __post_init__(self) -> None:
        # Snapshot walk-time identity so a parent swap cannot redirect a read.
        if self.path is not None:
            utf8_path(str(self.path))
            metadata = self.path.lstat()
            object.__setattr__(self, "metadata", metadata)
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(self.path)
                # A target that is not UTF-8 cannot be written identically by
                # both SDKs (Node reads it with replacement characters).
                utf8_path(target)
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(self.relative), target))
                if os.path.isabs(target) or resolved == ".." or resolved.startswith("../"):
                    raise create_error(
                        f"symbolic link leaves its local source: {json.dumps(str(self.path), ensure_ascii=False)}",
                        400,
                        "BAD_REQUEST",
                        "SDK",
                    )
                object.__setattr__(self, "link_target", target)
            elif not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise create_error(
                    f"unsupported file type in build context: {json.dumps(str(self.path), ensure_ascii=False)}",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                )


class _CappedFile(io.RawIOBase):
    """Stop streaming at cancellation or the upload limit before filling disk."""

    def __init__(self, path: Path, cancel: Event | None = None) -> None:
        self._cancel = cancel
        self._file = path.open("xb")
        self._written = 0

    def writable(self) -> bool:
        return True

    def write(self, data: object) -> int:
        if self._cancel is not None and self._cancel.is_set():
            raise TransferCancelledError()
        view = memoryview(data)  # type: ignore[arg-type]  # tarfile writes bytes-like objects
        self._written += view.nbytes
        if self._written > _context_upload.MAX_CONTEXT_BYTES:
            raise create_error("build context exceeds the 1 GiB limit", 400, "BAD_REQUEST", "SDK")
        return self._file.write(view)

    def close(self) -> None:
        # IOBase finalizes even when opening the destination failed in __init__.
        handle = getattr(self, "_file", None)
        if handle is not None:
            handle.close()
        super().close()


def _write_archive(entries: Iterable[_Entry], destination: Path, cancel: Event | None = None) -> None:
    # Stream mode: the capped writer is not seekable and a tar is front-to-back.
    with (
        _CappedFile(destination, cancel) as output,
        tarfile.open(fileobj=output, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        for entry in sorted(entries, key=lambda item: item.name):
            info = tarfile.TarInfo(entry.name)
            # TarInfo defaults zero ownership/times and empty owner names.
            if entry.content is not None:
                info.mode = 0o644
                info.size = len(entry.content)
                archive.addfile(info, io.BytesIO(entry.content))
            elif entry.path is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                _add_path(archive, info, entry)


def _add_path(archive: tarfile.TarFile, info: tarfile.TarInfo, entry: _Entry) -> None:
    assert entry.path is not None
    assert entry.metadata is not None
    metadata = entry.metadata
    info.mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        info.type = tarfile.SYMTYPE
        info.name = info.name.rstrip("/")
        info.linkname = entry.link_target
        archive.addfile(info)
    elif stat.S_ISDIR(metadata.st_mode):
        info.type = tarfile.DIRTYPE
        info.name = info.name.rstrip("/") + "/"
        archive.addfile(info)
    elif stat.S_ISREG(metadata.st_mode):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        quoted = json.dumps(str(entry.path), ensure_ascii=False)
        try:
            descriptor = os.open(entry.path, flags)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EPERM):
                message = f"build context file is not readable: {quoted}"
            elif error.errno in (errno.ELOOP, errno.ENOENT, errno.ENXIO, errno.ENOTDIR):
                message = f"build context changed: {quoted}"
            else:
                raise
            raise create_error(message, 400, "BAD_REQUEST", "SDK") from error
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise create_error(f"build context changed: {quoted}", 400, "BAD_REQUEST", "SDK")
            info.size = opened.st_size
            try:
                archive.addfile(info, handle)
            except OSError as error:
                # tarfile's sized copy reports a short read without an errno.
                if error.errno is not None or str(error) != "unexpected end of data":
                    raise
                raise create_error(f"build context changed: {quoted}", 400, "BAD_REQUEST", "SDK") from error
