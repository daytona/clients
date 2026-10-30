# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import tarfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_async_storage():
    from daytona._async.object_storage import AsyncObjectStorage

    with patch("daytona._async.object_storage.S3Store") as mock_store_cls:
        mock_store = MagicMock()
        mock_store.head_async = AsyncMock()
        mock_store.put_async = AsyncMock()
        mock_store_cls.return_value = mock_store
        storage = AsyncObjectStorage(
            "https://s3.example", "key", "secret", "token", bucket_name="bucket", region="us-east-2"
        )
    return storage, mock_store


class TestAsyncObjectStorage:
    def test_constructor_configures_store(self):
        from daytona._async.object_storage import AsyncObjectStorage

        with patch("daytona._async.object_storage.S3Store") as mock_store_cls:
            AsyncObjectStorage("https://s3.us-west-2.amazonaws.com", "key", "secret", "token", region="ap-south-1")

        assert mock_store_cls.call_args.kwargs["region"] == "ap-south-1"
        assert mock_store_cls.call_args.kwargs["client_options"]["timeout"] == timedelta(minutes=2)
        assert mock_store_cls.call_args.kwargs["retry_config"]["retry_timeout"] == timedelta(minutes=4)

    def test_retry_budget_stays_within_credential_lifetime(self):
        from daytona._async import object_storage

        assert object_storage.UPLOAD_RETRY_TIMEOUT < timedelta(minutes=5)

    def test_chunk_size_respects_s3_minimum_part_size(self):
        from daytona._async import object_storage

        assert object_storage.UPLOAD_CHUNK_SIZE >= 5 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_upload_missing_path_raises(self):
        storage, _store = _make_async_storage()

        with pytest.raises(FileNotFoundError, match="Path does not exist"):
            await storage.upload("/missing/path", "org-1")

    def test_compute_archive_base_path_trims_root_prefixes(self):
        from daytona._async.object_storage import AsyncObjectStorage

        assert AsyncObjectStorage.compute_archive_base_path("/workspace/project") == "workspace/project"
        windows_style = AsyncObjectStorage.compute_archive_base_path(r"\\workspace\\project")
        assert windows_style.startswith("workspace")
        assert not windows_style.startswith("\\")

    @pytest.mark.asyncio
    async def test_file_exists_in_s3_true_when_head_succeeds(self):
        storage, store = _make_async_storage()

        assert await storage._file_exists_in_s3("org/hash/context.tar") is True
        store.head_async.assert_awaited_once_with("org/hash/context.tar")

    @pytest.mark.asyncio
    async def test_file_exists_in_s3_false_when_head_raises_not_found(self):
        storage, store = _make_async_storage()
        store.head_async.side_effect = FileNotFoundError()

        assert await storage._file_exists_in_s3("org/hash/context.tar") is False

    @pytest.mark.asyncio
    async def test_compute_hash_for_file_changes_with_contents(self, tmp_path: Path):
        storage, _store = _make_async_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("one", encoding="utf-8")

        first_hash = await storage._compute_hash_for_path_md5(str(file_path))
        file_path.write_text("two", encoding="utf-8")
        second_hash = await storage._compute_hash_for_path_md5(str(file_path))

        assert first_hash != second_hash

    @pytest.mark.asyncio
    async def test_async_os_walk_returns_directory_entries(self, tmp_path: Path):
        storage, _store = _make_async_storage()
        directory = tmp_path / "dir"
        directory.mkdir()
        (directory / "a.txt").write_text("a", encoding="utf-8")

        walked = await storage._async_os_walk(str(directory))

        assert any(root == str(directory) and files == ["a.txt"] for root, _dirs, files in walked)

    @pytest.mark.asyncio
    async def test_upload_returns_existing_hash_without_uploading(self, tmp_path: Path):
        storage, _store = _make_async_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello", encoding="utf-8")
        storage._compute_hash_for_path_md5 = AsyncMock(return_value="hash123")
        storage._file_exists_in_s3 = AsyncMock(return_value=True)
        storage._upload_as_tar = AsyncMock()

        result = await storage.upload(str(file_path), "org-1")

        assert result == "hash123"
        storage._file_exists_in_s3.assert_awaited_once_with("org-1/hash123/context.tar")
        storage._upload_as_tar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_calls_tar_when_object_missing(self, tmp_path: Path):
        storage, _store = _make_async_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello", encoding="utf-8")
        storage._compute_hash_for_path_md5 = AsyncMock(return_value="hash123")
        storage._file_exists_in_s3 = AsyncMock(return_value=False)
        storage._upload_as_tar = AsyncMock()

        result = await storage.upload(str(file_path), "org-1", archive_base_path="custom/base")

        assert result == "hash123"
        storage._compute_hash_for_path_md5.assert_awaited_once_with(str(file_path), "custom/base", None)
        storage._upload_as_tar.assert_awaited_once_with(
            "org-1/hash123/context.tar", str(file_path), "custom/base", None
        )

    @pytest.mark.asyncio
    async def test_upload_as_tar_streams_content_to_store(self, tmp_path: Path):
        storage, store = _make_async_storage()
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello world", encoding="utf-8")

        captured: dict[str, bytes] = {}

        async def consume_stream(key: str, chunks, **_kwargs):
            data = bytearray()
            async for chunk in chunks:
                data.extend(chunk)
            captured[key] = bytes(data)

        store.put_async.side_effect = consume_stream

        await storage._upload_as_tar("org/hash/context.tar", str(file_path), "base/file.txt")

        assert "org/hash/context.tar" in captured
        assert len(captured["org/hash/context.tar"]) > 0
        assert store.put_async.call_args.kwargs["max_concurrency"] == 4
        assert store.put_async.call_args.kwargs["chunk_size"] == 5 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_upload_as_tar_excludes_dockerignore_matches(self, tmp_path: Path):
        storage, store = _make_async_storage()
        context = tmp_path / "env"
        (context / ".venv/bin").mkdir(parents=True)
        (context / ".venv/bin/python3").write_text("host interpreter", encoding="utf-8")
        (context / "app.py").write_text("print('hello')", encoding="utf-8")
        dockerignore = context / ".dockerignore"
        dockerignore.write_text(".venv/\n", encoding="utf-8")

        captured: dict[str, bytes] = {}

        async def consume_stream(key: str, chunks, **_kwargs):
            data = bytearray()
            async for chunk in chunks:
                data.extend(chunk)
            captured[key] = bytes(data)

        store.put_async.side_effect = consume_stream

        await storage._upload_as_tar("org/hash/context.tar", str(context), "env", str(dockerignore))

        with tarfile.open(fileobj=io.BytesIO(captured["org/hash/context.tar"])) as archive:
            names = archive.getnames()
        assert "env/app.py" in names
        assert not any(".venv" in name for name in names)

    @pytest.mark.asyncio
    async def test_ignored_files_do_not_change_context_hash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        storage, _store = _make_async_storage()
        context = tmp_path / "env"
        (context / ".venv").mkdir(parents=True)
        ignored_file = context / ".venv/python3"
        ignored_file.write_text("first", encoding="utf-8")
        (context / "app.py").write_text("print('hello')", encoding="utf-8")
        (context / ".dockerignore").write_text(".venv\n", encoding="utf-8")
        # Relative paths on both sides, as from_dockerfile("Dockerfile") produces them.
        monkeypatch.chdir(tmp_path)

        first_hash = await storage._compute_hash_for_path_md5("env", "env", "env/.dockerignore")
        ignored_file.write_text("second", encoding="utf-8")

        assert await storage._compute_hash_for_path_md5("env", "env", "env/.dockerignore") == first_hash
        (context / "app.py").write_text("print('changed')", encoding="utf-8")
        assert await storage._compute_hash_for_path_md5("env", "env", "env/.dockerignore") != first_hash

    @pytest.mark.asyncio
    async def test_hash_matches_sync_storage(self, tmp_path: Path):
        from daytona._sync.object_storage import ObjectStorage

        storage, _store = _make_async_storage()
        context = tmp_path / "env"
        (context / ".venv").mkdir(parents=True)
        (context / ".venv/python3").write_text("host interpreter", encoding="utf-8")
        (context / "app.py").write_text("print('hello')", encoding="utf-8")
        (context / ".dockerignore").write_text(".venv\n", encoding="utf-8")
        dockerignore = str(context / ".dockerignore")

        with patch("daytona._sync.object_storage.S3Store"):
            sync_storage = ObjectStorage("https://s3.example", "key", "secret", "token", region="us-east-2")

        for ignore_path in (None, dockerignore):
            assert await storage._compute_hash_for_path_md5(
                str(context), "env", ignore_path
            ) == sync_storage._compute_hash_for_path_md5(str(context), "env", ignore_path)
