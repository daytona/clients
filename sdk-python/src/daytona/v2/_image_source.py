# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Local image-builder inputs shared with the build-context archiver."""

from __future__ import annotations

import errno
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from typing_extensions import assert_never

from .errors import create_error


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalSource:
    path: Path
    kind: Literal["file", "dir"]
    archive_path: str
    # Whether the declared path itself was a symbolic link: a path that was not
    # one must not become one before the build (it would redirect the copy).
    link: bool = False
    # Its real path when added (os.path.realpath): a non-link source whose real
    # path differs at build time had an ancestor swapped and is refused.
    real: str = ""


def expand_home(path: str) -> str:
    """Expand the current user's home prefix, but never a named user's home."""
    if path == "~" or path.startswith("~/") or (os.name == "nt" and path.startswith("~\\")):
        return os.path.expanduser(path)
    return path


def utf8_path(path: str) -> None:
    """Refuse paths that cannot be serialized as UTF-8, before filesystem use."""
    if any("\ud800" <= char <= "\udfff" for char in path):
        # Recover surrogateescaped OS bytes, then decode just like Node's Buffer.
        escaped = "".join(
            "�" if "\ud800" <= char < "\udc80" or "\udcff" < char <= "\udfff" else char for char in path
        )
        display = escaped.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        raise create_error(
            f"build context path is not valid UTF-8: {json.dumps(display, ensure_ascii=False)}",
            400,
            "BAD_REQUEST",
            "SDK",
        )


def local_source(path: str | os.PathLike[str], kind: Literal["file", "dir"], index: int) -> LocalSource:
    """Check a source now without resolving symbolic links or reading its contents."""
    try:
        given = os.fspath(path)
    except TypeError as error:
        raise create_error(
            "ImageBuilder localPath must be a non-empty string", 400, "BAD_REQUEST", "SDK"
        ) from error
    if not isinstance(given, str) or not given.strip():
        raise create_error("ImageBuilder localPath must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    if "\0" in given:
        raise create_error(
            "ImageBuilder localPath must not contain a NUL character", 400, "BAD_REQUEST", "SDK"
        )
    absolute = Path(os.path.abspath(expand_home(given)))
    utf8_path(str(absolute))
    try:
        info = os.stat(absolute)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            raise create_error(f"{absolute} does not exist", 400, "BAD_REQUEST", "SDK") from error
        if error.errno in (errno.EACCES, errno.EPERM):
            raise create_error(
                f"build context file is not readable: {json.dumps(str(absolute), ensure_ascii=False)}",
                400,
                "BAD_REQUEST",
                "SDK",
            ) from error
        raise
    match kind:
        case "file":
            valid = stat.S_ISREG(info.st_mode)
            description = "file"
            safe = not any(
                char in "$\\*?[]'\"" or char < " " or "\x7f" <= char <= "\x9f" for char in absolute.name
            )
            archive_path = f".daytona-context/{index}/{absolute.name if safe else 'file'}"
        case "dir":
            valid = stat.S_ISDIR(info.st_mode)
            description = "directory"
            archive_path = f".daytona-context/{index}"
        case unreachable:
            assert_never(unreachable)
    if not valid:
        raise create_error(
            f"{absolute} is not a {description}",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return LocalSource(
        path=absolute,
        kind=kind,
        archive_path=archive_path,
        link=os.path.islink(absolute),
        real=os.path.realpath(absolute),
    )
