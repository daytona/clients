# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Local builder and directory contexts through the real HTTP upload path."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import anyio
import pytest
from test_build_context_upload import UPLOAD_ID, Peer, build, part_number, storage_peer

from daytona.v2 import BadRequestError, ImageBuilder
from daytona.v2._bridge import SyncBridge
from daytona.v2._sync.services import ImagesService
from daytona.v2._transport import AsyncTransport
from daytona.v2.services import AsyncImagesService
from daytona.v2.types import Image


@pytest.fixture(params=[False, True], ids=["async", "generated-sync"])
def sync(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


async def build_builder(
    peer: Peer,
    builder: ImageBuilder,
    sync: bool,
    *,
    name: str | None = None,
    idle_expiry_seconds: int | None = None,
) -> Image:
    if sync:

        def run() -> Image:
            bridge = SyncBridge()

            async def make_transport() -> AsyncTransport:
                return AsyncTransport(peer.url, "offline")

            transport = bridge.call(make_transport)
            try:
                return ImagesService(AsyncImagesService(transport), bridge).build(
                    builder, name=name, idle_expiry_seconds=idle_expiry_seconds
                )
            finally:
                bridge.close(transport.close)

        return await anyio.to_thread.run_sync(run)
    transport = AsyncTransport(peer.url, "offline")
    try:
        return await AsyncImagesService(transport).build(
            builder, name=name, idle_expiry_seconds=idle_expiry_seconds
        )
    finally:
        await transport.close()


def uploaded_bytes(peer: Peer) -> bytes:
    puts = [(path, body) for method, path, _, body in peer.requests if method == "PUT"]
    return b"".join(body for path, body in sorted(puts, key=lambda put: part_number(put[0])))


@pytest.mark.anyio
async def test_builder_files_are_uploaded_and_the_verified_context_is_built(
    sync: bool, tmp_path: Path
) -> None:
    # Given a builder with a local source larger than a storage part.
    source = tmp_path / "input.txt"
    source.write_bytes(b"local-data\n" * 1000)
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/input.txt")
    with storage_peer() as peer:
        # When the builder is submitted through either SDK surface.
        image = await build_builder(peer, builder, sync, name="local-image")
    # Then the declaration describes exactly the tar carried by every part.
    data = uploaded_bytes(peer)
    assert peer.json("POST", "/build-contexts") == {
        "dockerfile": "Dockerfile",
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for name, expected in (
            ("Dockerfile", builder.to_dockerfile().encode()),
            (".daytona-context/1/input.txt", b"local-data\n" * 1000),
        ):
            stream = archive.extractfile(name)
            assert stream is not None
            with stream:
                assert stream.read() == expected
    assert peer.json("POST", f"/build-contexts/{UPLOAD_ID}/complete") == {"region_id": "region-c"}
    assert peer.json("POST", "/images") == {
        "context_upload_id": UPLOAD_ID,
        "region_id": "region-c",
        "name": "local-image",
    }
    assert image.id == "img-ctx"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [False, True], ids=["success", "upload-refused"])
@pytest.mark.parametrize("directory", [False, True], ids=["builder", "directory"])
async def test_temporary_context_archive_is_removed(
    sync: bool, failure: bool, directory: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given local sources and a recorder at the archive writer's narrow seam.
    from threading import Event

    from daytona.v2 import _image_context

    destinations: list[Path] = []
    original = _image_context.write_builder_context
    original_directory = _image_context.write_directory_context

    def record(builder: ImageBuilder, destination: Path, *, cancel: Event | None = None) -> None:
        destinations.append(destination)
        original(builder, destination, cancel=cancel)

    def record_directory(
        root: Path, dockerfile: str, destination: Path, *, cancel: Event | None = None
    ) -> None:
        destinations.append(destination)
        original_directory(root, dockerfile, destination, cancel=cancel)

    monkeypatch.setattr(_image_context, "write_builder_context", record)
    monkeypatch.setattr(_image_context, "write_directory_context", record_directory)
    source = tmp_path / "file"
    source.write_text("local")
    (tmp_path / "Dockerfile").write_text("FROM alpine\n")
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/file")
    with storage_peer(put_statuses=[403] if failure else []) as peer:

        async def submit() -> None:
            if directory:
                await build(peer, tmp_path, sync)
            else:
                await build_builder(peer, builder, sync)

        # When the upload succeeds or storage refuses it.
        if failure:
            with pytest.raises(BadRequestError):
                await submit()
        else:
            await submit()
    # Then the owned temporary archive is gone in both outcomes.
    assert destinations
    assert all(not path.exists() and not path.parent.exists() for path in destinations)


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_pair", [False, True], ids=["blank-name", "named-idle-expiry"])
async def test_builder_options_are_refused_before_source_reads_or_requests(
    sync: bool, invalid_pair: bool, tmp_path: Path
) -> None:
    # Given a builder whose file vanished after declaration and invalid options.
    source = tmp_path / "file"
    source.write_text("local")
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/file")
    source.unlink()
    # When the build is called, option validation wins over opening the file.
    with storage_peer() as peer, pytest.raises(BadRequestError) as raised:
        await build_builder(
            peer,
            builder,
            sync,
            name="named" if invalid_pair else " ",
            idle_expiry_seconds=60 if invalid_pair else None,
        )
    # Then neither archiving nor network activity hid the caller's mistake.
    assert "idle_expiry_seconds" in str(raised.value) if invalid_pair else "name" in str(raised.value)
    assert "file" not in str(raised.value)
    assert peer.requests == []


@pytest.mark.anyio
async def test_builder_without_files_never_invokes_the_context_writer(
    sync: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a Dockerfile-only builder and a writer that must not be needed.
    from daytona.v2 import _image_context

    def refuse(builder: ImageBuilder, destination: Path) -> None:
        pytest.fail("a builder without local sources must not be archived")

    monkeypatch.setattr(_image_context, "write_builder_context", refuse)
    builder = ImageBuilder.from_registry("alpine").user("nobody")
    with storage_peer() as peer:
        # When the image is submitted.
        await build_builder(peer, builder, sync)
    # Then the original Dockerfile path remains the only build request.
    assert peer.json("POST", "/images") == {"dockerfile": "FROM alpine\nUSER nobody\n"}
    assert peer.bodies("POST", "/build-contexts") == []


@pytest.mark.anyio
@pytest.mark.parametrize("specific", [False, True], ids=["root-ignore", "dockerfile-ignore"])
async def test_directory_context_selects_its_ignore_and_keeps_required_files(
    sync: bool, specific: bool, tmp_path: Path
) -> None:
    # Given opposing root and Dockerfile-specific ignore rules.
    root = tmp_path / "project"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM alpine\nCOPY . /app\n")
    (root / ".dockerignore").write_text("Dockerfile\n.dockerignore\nroot-only\n")
    (root / "root-only").write_text("root")
    (root / "specific-only").write_text("specific")
    if specific:
        (root / "Dockerfile.dockerignore").write_text("Dockerfile\nDockerfile.dockerignore\nspecific-only\n")
    with storage_peer() as peer:
        # When the directory is built through the actual multipart upload flow.
        await build(peer, root, sync)
    # Then only the selected ignore governs, but required inputs always survive.
    data = uploaded_bytes(peer)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        names = set(archive.getnames())
        assert "Dockerfile" in names
        assert ("root-only" in names) is specific
        assert ("specific-only" in names) is not specific
        assert ("Dockerfile.dockerignore" if specific else ".dockerignore") in names
    declared = peer.json("POST", "/build-contexts")
    assert declared["bytes"] == len(data)
    assert declared["sha256"] == hashlib.sha256(data).hexdigest()
    assert peer.json("POST", "/images") == {"context_upload_id": UPLOAD_ID, "region_id": "region-c"}


@pytest.mark.anyio
async def test_unchanged_directory_context_reuses_the_same_digest(sync: bool, tmp_path: Path) -> None:
    # Given an unchanged directory submitted twice.
    (tmp_path / "Dockerfile").write_text("FROM alpine\n")
    (tmp_path / "payload").write_text("stable")
    with storage_peer() as first, storage_peer(ready=True) as second:
        # When independently archived, with the second declaration already ready.
        await build(first, tmp_path, sync)
        os.utime(tmp_path / "payload", (12345, 98765))
        await build(second, tmp_path, sync)
    # Then the same declaration reuses the verified archive without another PUT.
    assert first.json("POST", "/build-contexts") == second.json("POST", "/build-contexts")
    assert second.bodies("PUT", "/") == []


@pytest.mark.anyio
@pytest.mark.parametrize("as_path", [False, True], ids=["string", "path"])
async def test_context_path_reports_utf8_error_when_it_contains_a_lone_surrogate(
    sync: bool, as_path: bool, tmp_path: Path
) -> None:
    # Given: a path that cannot be probed or uploaded as UTF-8.
    given = str(tmp_path / "bad\ud800")
    context = Path(given) if as_path else given
    # When: preparing it through either public surface.
    with storage_peer() as peer, pytest.raises(BadRequestError) as raised:
        await build(peer, context, sync)
    # Then: validation wins over is_dir and no request is made.
    assert str(raised.value) == "build context path is not valid UTF-8: " + json.dumps(
        given.replace("\ud800", "�"), ensure_ascii=False
    )
    assert peer.requests == []


@pytest.mark.anyio
async def test_build_from_context_refuses_an_image_builder_before_any_request() -> None:
    # A builder is built with images.build(); TypeScript refuses it the same way.
    from daytona.v2 import ImageBuilder
    from daytona.v2.errors import BadRequestError

    with storage_peer() as peer:
        with pytest.raises(BadRequestError, match=r"images\.build\(\)"):
            await build(peer, ImageBuilder.from_registry("alpine"), False)
        assert peer.requests == []
