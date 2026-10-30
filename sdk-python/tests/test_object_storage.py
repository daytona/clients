# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import tarfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_storage():
    from daytona._sync.object_storage import ObjectStorage

    with patch("daytona._sync.object_storage.S3Store") as mock_store_cls:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        storage = ObjectStorage(
            "https://s3.example", "key", "secret", "token", bucket_name="bucket", region="us-east-2"
        )
    return storage, mock_store


class TestObjectStorage:
    def test_constructor_configures_store(self):
        from daytona._sync.object_storage import ObjectStorage

        with patch("daytona._sync.object_storage.S3Store") as mock_store_cls:
            ObjectStorage("https://s3.us-west-2.amazonaws.com", "key", "secret", "token", region="ap-south-1")

        assert mock_store_cls.call_args.kwargs["region"] == "ap-south-1"
        assert mock_store_cls.call_args.kwargs["client_options"]["timeout"] == timedelta(minutes=2)
        assert mock_store_cls.call_args.kwargs["retry_config"]["retry_timeout"] == timedelta(minutes=4)

    def test_retry_budget_stays_within_credential_lifetime(self):
        from daytona._sync import object_storage

        assert object_storage.UPLOAD_RETRY_TIMEOUT < timedelta(minutes=5)

    def test_chunk_size_respects_s3_minimum_part_size(self):
        from daytona._sync import object_storage

        assert object_storage.UPLOAD_CHUNK_SIZE >= 5 * 1024 * 1024

    def test_constructor_requires_region(self):
        from daytona._sync.object_storage import ObjectStorage

        with pytest.raises(TypeError):
            ObjectStorage(
                "https://s3.us-west-2.amazonaws.com", "key", "secret", "token"
            )  # pylint: disable=missing-kwoa

    def test_upload_missing_path_raises(self):
        storage, _store = _make_storage()

        with pytest.raises(FileNotFoundError, match="Path does not exist"):
            storage.upload("/missing/path", "org-1")

    def test_compute_archive_base_path_trims_root_prefixes(self):
        from daytona._sync.object_storage import ObjectStorage

        assert ObjectStorage.compute_archive_base_path("/workspace/project") == "workspace/project"
        windows_style = ObjectStorage.compute_archive_base_path(r"\\workspace\\project")
        assert windows_style.startswith("workspace")
        assert not windows_style.startswith("\\")

    def test_file_exists_in_s3_true_when_head_succeeds(self):
        storage, store = _make_storage()

        assert storage._file_exists_in_s3("org/hash/context.tar") is True
        store.head.assert_called_once_with("org/hash/context.tar")

    def test_file_exists_in_s3_false_when_head_raises_not_found(self):
        storage, store = _make_storage()
        store.head.side_effect = FileNotFoundError()

        assert storage._file_exists_in_s3("org/hash/context.tar") is False

    def test_compute_hash_for_file_changes_with_contents(self, tmp_path: Path):
        storage, _store = _make_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("one", encoding="utf-8")

        first_hash = storage._compute_hash_for_path_md5(str(file_path))
        file_path.write_text("two", encoding="utf-8")
        second_hash = storage._compute_hash_for_path_md5(str(file_path))

        assert first_hash != second_hash

    def test_compute_hash_for_directory_changes_with_new_file(self, tmp_path: Path):
        storage, _store = _make_storage()
        directory = tmp_path / "dir"
        directory.mkdir()
        (directory / "a.txt").write_text("a", encoding="utf-8")

        first_hash = storage._compute_hash_for_path_md5(str(directory))
        (directory / "b.txt").write_text("b", encoding="utf-8")
        second_hash = storage._compute_hash_for_path_md5(str(directory))

        assert first_hash != second_hash

    def test_upload_returns_existing_hash_without_uploading(self, tmp_path: Path):
        storage, _store = _make_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello", encoding="utf-8")
        storage._compute_hash_for_path_md5 = MagicMock(return_value="hash123")
        storage._file_exists_in_s3 = MagicMock(return_value=True)
        storage._upload_as_tar = MagicMock()

        result = storage.upload(str(file_path), "org-1")

        assert result == "hash123"
        storage._file_exists_in_s3.assert_called_once_with("org-1/hash123/context.tar")
        storage._upload_as_tar.assert_not_called()

    def test_upload_uses_archive_base_path_when_uploading(self, tmp_path: Path):
        storage, _store = _make_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello", encoding="utf-8")
        storage._compute_hash_for_path_md5 = MagicMock(return_value="hash123")
        storage._file_exists_in_s3 = MagicMock(return_value=False)
        storage._upload_as_tar = MagicMock()

        result = storage.upload(str(file_path), "org-1", archive_base_path="custom/base")

        assert result == "hash123"
        storage._compute_hash_for_path_md5.assert_called_once_with(str(file_path), "custom/base", None)
        storage._upload_as_tar.assert_called_once_with("org-1/hash123/context.tar", str(file_path), "custom/base", None)

    def test_upload_as_tar_streams_content_to_store(self, tmp_path: Path):
        storage, store = _make_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello world", encoding="utf-8")

        captured: dict[str, bytes] = {}

        def consume_stream(key: str, chunks, **_kwargs):
            captured[key] = b"".join(chunks)

        store.put.side_effect = consume_stream

        storage._upload_as_tar("org/hash/context.tar", str(file_path), "base/file.txt")

        assert "org/hash/context.tar" in captured
        assert len(captured["org/hash/context.tar"]) > 0
        assert store.put.call_args.kwargs["max_concurrency"] == 4
        assert store.put.call_args.kwargs["chunk_size"] == 5 * 1024 * 1024

    def test_upload_as_tar_excludes_dockerignore_matches(self, tmp_path: Path):
        storage, store = _make_storage()
        context = tmp_path / "env"
        (context / ".venv/bin").mkdir(parents=True)
        (context / ".venv/bin/python3").write_text("host interpreter", encoding="utf-8")
        (context / "app.py").write_text("print('hello')", encoding="utf-8")
        dockerignore = context / ".dockerignore"
        dockerignore.write_text(".venv/\n", encoding="utf-8")

        captured: dict[str, bytes] = {}

        def consume_stream(key: str, chunks, **_kwargs):
            captured[key] = b"".join(chunks)

        store.put.side_effect = consume_stream

        storage._upload_as_tar("org/hash/context.tar", str(context), "env", str(dockerignore))

        with tarfile.open(fileobj=io.BytesIO(captured["org/hash/context.tar"])) as archive:
            names = archive.getnames()
        assert "env/app.py" in names
        assert not any(".venv" in name for name in names)

    def test_upload_as_tar_without_dockerignore_rules_is_unchanged(self, tmp_path: Path):
        storage, store = _make_storage()
        context = tmp_path / "env"
        (context / "pkg").mkdir(parents=True)
        (context / "pkg/mod.py").write_text("x = 1", encoding="utf-8")
        (context / "app.py").write_text("print('hello')", encoding="utf-8")
        (context / "empty").mkdir()
        (context / ".dockerignore").write_text("# only a comment\n\n", encoding="utf-8")

        captured: list[bytes] = []
        store.put.side_effect = lambda _key, chunks, **_kwargs: captured.append(b"".join(chunks))

        storage._upload_as_tar("org/hash/context.tar", str(context), "env")
        storage._upload_as_tar("org/hash/context.tar", str(context), "env", str(context / ".dockerignore"))
        storage._upload_as_tar("org/hash/context.tar", str(context), "env", str(context / "missing"))

        assert captured[0] == captured[1] == captured[2]

    def test_hash_without_dockerignore_rules_is_unchanged(self, tmp_path: Path):
        storage, _store = _make_storage()
        context = tmp_path / "env"
        context.mkdir()
        (context / "app.py").write_text("print('hello')", encoding="utf-8")
        (context / ".dockerignore").write_text("# only a comment\n", encoding="utf-8")

        legacy = storage._compute_hash_for_path_md5(str(context), "env")

        assert storage._compute_hash_for_path_md5(str(context), "env", str(context / ".dockerignore")) == legacy
        assert storage._compute_hash_for_path_md5(str(context), "env", str(context / "missing")) == legacy

    def test_ignored_files_do_not_change_context_hash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        storage, _store = _make_storage()
        context = tmp_path / "env"
        (context / ".venv").mkdir(parents=True)
        ignored_file = context / ".venv/python3"
        ignored_file.write_text("first", encoding="utf-8")
        (context / "app.py").write_text("print('hello')", encoding="utf-8")
        (context / ".dockerignore").write_text(".venv\n*.log\n", encoding="utf-8")
        # Relative paths on both sides, as from_dockerfile("Dockerfile") produces them.
        monkeypatch.chdir(tmp_path)

        def context_hash() -> str:
            return storage._compute_hash_for_path_md5("env", "env", "env/.dockerignore")

        first_hash = context_hash()
        ignored_file.write_text("second", encoding="utf-8")
        (context / "debug.log").write_text("noise", encoding="utf-8")

        assert context_hash() == first_hash
        assert storage._compute_hash_for_path_md5(str(context), "env", str(context / ".dockerignore")) == first_hash

        (context / "app.py").write_text("print('changed')", encoding="utf-8")
        assert context_hash() != first_hash

    def test_hash_follows_directories_the_archive_keeps(self, tmp_path: Path):
        storage, _store = _make_storage()
        context = tmp_path / "env"
        context.mkdir()
        (context / ".dockerignore").write_text("*.log\n", encoding="utf-8")
        dockerignore = str(context / ".dockerignore")

        first_hash = storage._compute_hash_for_path_md5(str(context), "env", dockerignore)
        (context / "logs").mkdir()

        assert storage._compute_hash_for_path_md5(str(context), "env", dockerignore) != first_hash
