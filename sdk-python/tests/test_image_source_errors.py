# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Typed errors at local-source declaration and archive boundaries."""

import errno
import json
import os
from pathlib import Path

import pytest

from daytona.v2 import BadRequestError, ImageBuilder
from daytona.v2._image_context import write_builder_context


@pytest.mark.parametrize("method", ["add_local_file", "add_local_dir", "pip_install_from_requirements"])
def test_nul_path_is_refused_when_declaring_source(method: str) -> None:
    # Given a local source spelling the filesystem cannot accept.
    builder = ImageBuilder.from_registry("alpine")
    args = ("a\0b",) if method == "pip_install_from_requirements" else ("a\0b", "/out")
    # When declared, then the SDK rejects it without exposing the NUL in its message.
    with pytest.raises(BadRequestError) as raised:
        getattr(builder, method)(*args)
    assert str(raised.value) == "ImageBuilder localPath must not contain a NUL character"


@pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM, errno.EIO])
@pytest.mark.parametrize("method", ["add_local_file", "add_local_dir", "pip_install_from_requirements"])
def test_stat_failure_is_classified_when_declaring_source(tmp_path: Path, code: int, method: str) -> None:
    # Given a declaration-time OS failure, independent of the process's privileges.
    source = tmp_path / 'source"é'
    failure = OSError(code, "test stat failure", source)
    builder = ImageBuilder.from_registry("alpine")
    args = (source,) if method == "pip_install_from_requirements" else (source, "/out")

    def refuse_stat(path: str | os.PathLike[str]) -> os.stat_result:
        raise failure

    # When declared, then permissions are typed and unrelated I/O still propagates.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(os, "stat", refuse_stat)
        if code == errno.EIO:
            with pytest.raises(OSError) as raised_io:
                getattr(builder, method)(*args)
            assert raised_io.value is failure
        else:
            with pytest.raises(BadRequestError) as raised:
                getattr(builder, method)(*args)
            assert str(raised.value) == (
                f"build context file is not readable: {json.dumps(str(source), ensure_ascii=False)}"
            )


@pytest.mark.parametrize("method", ["add_local_file", "add_local_dir", "pip_install_from_requirements"])
@pytest.mark.parametrize("replace_parent", [False, True], ids=["removed", "parent-is-file"])
def test_missing_source_is_typed_when_archiving(tmp_path: Path, method: str, replace_parent: bool) -> None:
    # Given a declared source removed before the archive is prepared.
    parent = tmp_path / "parent"
    parent.mkdir()
    source = parent / 'source"é'
    builder = ImageBuilder.from_registry("alpine")
    if method == "add_local_dir":
        source.mkdir()
        builder = builder.add_local_dir(source, "/out")
        source.rmdir()
    else:
        source.write_bytes(b"content")
        builder = (
            builder.add_local_file(source, "/out")
            if method == "add_local_file"
            else builder.pip_install_from_requirements(source)
        )
        source.unlink()
    if replace_parent:
        parent.rmdir()
        parent.write_bytes(b"replacement")
    # When archived, then the original declared path is named in a typed error.
    with pytest.raises(BadRequestError) as raised:
        write_builder_context(builder, tmp_path / "context.tar")
    assert str(raised.value) == f"local source does not exist: {json.dumps(str(source), ensure_ascii=False)}"
