# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""One ``ImageBuilder`` rule in both SDKs: every value is a string that is not
blank by Python's whitespace set, and text rendered raw into a Dockerfile line
is stripped with that set. The fixture is shared with the TypeScript suite
(``sdk-typescript/tests/image-builder-parity.test.ts``), so both render the
same Dockerfile and request source.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError, ImageBuilder

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "image-builder-parity.json").read_text(encoding="utf-8")
)


def _build(case: dict[str, object]) -> ImageBuilder:
    source = case["source"]
    assert isinstance(source, dict)
    match source:
        case {"registry": ref}:
            builder = ImageBuilder.from_registry(ref)
        case {"dockerfile": content}:
            builder = ImageBuilder.from_dockerfile(content)
        case {"debianSlim": version}:
            builder = ImageBuilder.debian_slim(version)
        case _:
            raise AssertionError(f"unknown builder source: {source}")
    steps = case["steps"]
    assert isinstance(steps, list)
    for op, argument in steps:
        if op == "run":
            builder = builder.run_commands(*argument)
        elif op == "pip":
            builder = builder.pip_install(*argument)
        elif op == "env":
            builder = builder.env(argument)
        elif op == "env-name":
            builder = builder.env(*argument)
        elif op == "env-map":
            # The fixture's pairs as an iterable that is not a mapping: Python's
            # counterpart of a TypeScript Map.
            builder = builder.env(iter(argument))
        elif op == "user":
            builder = builder.user(argument)
        elif op == "cmd":
            builder = builder.cmd(*argument)
        elif op == "entrypoint":
            builder = builder.entrypoint(*argument)
        else:
            builder = builder.workdir(argument)
    return builder


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=[case["name"] for case in _FIXTURE["cases"]])
def test_the_builder_renders_the_shared_fixture(case: dict[str, object]) -> None:
    builder = _build(case)
    assert builder.to_dockerfile() == case["dockerfile"]
    source, dockerfile = builder._request_source()
    assert ({"source": source} if source is not None else {"dockerfile": dockerfile}) == case["request"]


@pytest.mark.parametrize("key", ["2", " 2"])
def test_env_preserves_mapping_order_when_keys_are_numeric(key: str) -> None:
    # Given: Python dicts retain insertion order, unlike JS integer keys.
    values = {"b": "1", key: "2", "a": "3"}
    # When: the mapping is rendered, including key stripping.
    dockerfile = ImageBuilder.from_registry("alpine").env(values).to_dockerfile()
    # Then: the caller's mapping order survives rendering.
    assert dockerfile == 'FROM alpine\nENV b="1"\nENV 2="2"\nENV a="3"\n'


@pytest.mark.parametrize("case", _FIXTURE["refused"], ids=[case["name"] for case in _FIXTURE["refused"]])
def test_the_builder_refuses_the_shared_fixture(case: dict[str, object]) -> None:
    with pytest.raises(BadRequestError) as raised:
        _build(case)
    assert str(raised.value) == case["error"]


@pytest.mark.parametrize(
    "call",
    [
        lambda: ImageBuilder.from_registry(None),  # type: ignore[arg-type]
        lambda: ImageBuilder.from_registry("\u0085"),
        lambda: ImageBuilder.from_dockerfile(5),  # type: ignore[arg-type]
        lambda: ImageBuilder.from_dockerfile(" \u001c "),
        lambda: ImageBuilder.from_registry("alpine").run_commands(None),  # type: ignore[arg-type]
        lambda: ImageBuilder.from_registry("alpine").pip_install(5),  # type: ignore[arg-type]
        lambda: ImageBuilder.from_registry("alpine").env({"A": 5}),  # type: ignore[dict-item]
        lambda: ImageBuilder.from_registry("alpine").env({5: "v"}),  # type: ignore[dict-item]
        lambda: ImageBuilder.from_registry("alpine").env("A", None),  # type: ignore[call-overload]
        lambda: ImageBuilder.from_registry("alpine").env(5),  # type: ignore[call-overload]
        lambda: ImageBuilder.from_registry("alpine").workdir(None),  # type: ignore[arg-type]
    ],
    ids=[
        "from_registry None",
        "from_registry U+0085",
        "from_dockerfile number",
        "from_dockerfile blank",
        "run_commands None",
        "pip_install number",
        "env value number",
        "env key number",
        "env value None",
        "env number",
        "workdir None",
    ],
)
def test_a_wrong_builder_value_is_a_bad_request(call: Callable[[], object]) -> None:
    with pytest.raises(BadRequestError) as raised:
        call()
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_images_pull_strips_the_ref_and_refuses_a_non_string_before_any_request() -> None:
    image = {
        "id": "img",
        "class": "linux",
        "status": "pending",
        "region_id": "r",
        "region_ids": ["r"],
        "anonymous": True,
        "retention": "idle",
        "origin": "registry",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "format_version": 2,
        "format_outdated": False,
        "platform": False,
        "default": False,
        "deprecated": False,
    }
    with FakeApi(
        [PlannedResponse(200, {"id": "img", "status": "pending"}), PlannedResponse(200, image)]
    ) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError):
                await root.v2.images.pull(5, wait=False)  # type: ignore[arg-type]
            assert fake.requests == []
            await root.v2.images.pull(" python:3.12 ", wait=False)
    assert json.loads(fake.requests[0].body)["source"] == {"type": "registry", "ref": "python:3.12"}


def test_env_refuses_a_mapping_with_a_second_argument() -> None:
    # The mapping carries its values; TypeScript refuses the same call.
    with pytest.raises(BadRequestError) as raised:
        values: Any = {"A": "1"}
        ImageBuilder.from_registry("alpine").env(values, "2")
    assert str(raised.value) == "ImageBuilder.env takes a name and a value, or a mapping of names to values"


def test_local_sources_survive_every_fluent_operation(tmp_path: Path) -> None:
    # Given: a file, a directory and an unchanged registry builder.
    file = tmp_path / "ž requirements.txt"
    file.write_text("requests", encoding="utf-8")
    directory = tmp_path / "src"
    directory.mkdir()
    original = ImageBuilder.from_registry("python:3.13")

    # When: local additions are interleaved with every existing fluent operation.
    built = (
        original.add_local_file(file, "/app/")
        .run_commands("true")
        .pip_install("requests")
        .env("A", "1")
        .env({"B": "2"})
        .workdir("/app")
        .user(" daytona ")
        .cmd("python", " padded ")
        .entrypoint("/bin/sh")
        .add_local_dir(directory, "/src")
        .pip_install_from_requirements(file)
    )

    # Then: source order agrees with COPY paths and the original stays unchanged.
    assert original._local_sources == ()
    assert [(item.path, item.kind, item.archive_path) for item in built._local_sources] == [
        (file, "file", ".daytona-context/1/ž requirements.txt"),
        (directory, "dir", ".daytona-context/2"),
        (file, "file", ".daytona-context/3/ž requirements.txt"),
    ]
    copies = [line for line in built.to_dockerfile().splitlines() if line.startswith("COPY ")]
    assert copies == [
        'COPY [".daytona-context/1/ž requirements.txt", "/app/ž requirements.txt"]',
        'COPY [".daytona-context/2", "/src"]',
        'COPY [".daytona-context/3/ž requirements.txt", "/.requirements.txt"]',
    ]
    assert built.to_dockerfile().endswith("RUN python -m pip install -r /.requirements.txt\n")


@pytest.mark.parametrize("method", ["add_local_file", "add_local_dir", "pip_install_from_requirements"])
@pytest.mark.parametrize("path", [None, 5, False, b"/etc/hostname", [], {}])
def test_non_string_local_paths_are_bad_requests(
    method: str, path: str | bytes | int | list[str] | dict[str, str] | None
) -> None:
    # Given a local-source method and a runtime value that is not a string path.
    builder = ImageBuilder.from_registry("alpine")
    arguments = (path,) if method == "pip_install_from_requirements" else (path, "/app")
    # When the source is added, then it is refused before any filesystem access.
    with pytest.raises(BadRequestError) as raised:
        getattr(builder, method)(*arguments)
    assert str(raised.value) == "ImageBuilder localPath must be a non-empty string"
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.parametrize("method", ["add_local_file", "add_local_dir", "pip_install_from_requirements"])
@pytest.mark.parametrize("existing_wrong_kind", [False, True])
def test_local_source_checks_existence_and_kind_at_call_time(
    tmp_path: Path, method: str, existing_wrong_kind: bool
) -> None:
    # Given: either a missing source or one of the wrong filesystem kind.
    path = tmp_path / "source"
    if existing_wrong_kind:
        if method == "add_local_dir":
            path.write_text("not a directory", encoding="utf-8")
        else:
            path.mkdir()
    builder = ImageBuilder.from_registry("alpine")

    # When: a local source is added, before building or reading any contents.
    with pytest.raises(BadRequestError) as raised:
        if method == "pip_install_from_requirements":
            builder.pip_install_from_requirements(path)
        else:
            getattr(builder, method)(path, "/app")

    # Then: the typed SDK error identifies the absolute source path.
    assert str(path) in str(raised.value)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


def test_local_source_expands_home_without_resolving_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a symlink in the caller's home directory.
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    monkeypatch.setenv("HOME", str(tmp_path))

    # When: the home-relative symlink is added.
    builder = ImageBuilder.from_registry("alpine").add_local_file("~/link.txt", "/app/")

    # Then: the source keeps the absolute link path, not its target.
    assert builder._local_sources[0].path == link
    assert builder._local_sources[0].archive_path == ".daytona-context/1/link.txt"
    with pytest.raises(FrozenInstanceError):
        builder._local_sources[0].archive_path = "changed"


def test_requirements_keyword_is_preserved_when_porting_0x(tmp_path: Path) -> None:
    # Given the keyword accepted by the 0.x image builder.
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("numpy\n")
    # When called by keyword, then it produces the requirements installation.
    builder = ImageBuilder.from_registry("alpine").pip_install_from_requirements(
        requirements_txt=requirements
    )
    assert builder.to_dockerfile().endswith("RUN python -m pip install -r /.requirements.txt\n")


def test_user_is_positional_only_when_called_by_keyword() -> None:
    # Given user follows workdir's positional-only surface.
    builder = ImageBuilder.from_registry("alpine")
    # When a caller supplies the keyword dynamically, then Python refuses it.
    with pytest.raises(TypeError, match="positional-only"):
        builder.user(name="nobody")


@pytest.mark.parametrize("name", ["price$HOME", "a\\b", "a*", "a?", "a[", "a]", "a\t", "a\x7f", "a\x85"])
def test_unsafe_basename_uses_fixed_source_when_declared(tmp_path: Path, name: str) -> None:
    # Given a filename unsafe for literal BuildKit source matching.
    source = tmp_path / name
    source.write_bytes(b"x")
    # When declared, then COPY cannot expand variables, globs or controls in it.
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/existing/dir")
    assert builder.to_dockerfile() == 'FROM alpine\nCOPY [".daytona-context/1/file", "/existing/dir"]\n'
