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
from pathlib import Path

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
    builder = (
        ImageBuilder.from_registry(source["registry"])
        if "registry" in source
        else ImageBuilder.from_dockerfile(source["dockerfile"])
    )
    steps = case["steps"]
    assert isinstance(steps, list)
    for op, argument in steps:
        if op == "run":
            builder = builder.run_commands(*argument)
        elif op == "pip":
            builder = builder.pip_install(*argument)
        elif op == "env":
            builder = builder.env(argument)
        elif op == "env-map":
            # The fixture's pairs as an iterable that is not a mapping: Python's
            # counterpart of a TypeScript Map.
            builder = builder.env(iter(argument))
        else:
            builder = builder.workdir(argument)
    return builder


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=[case["name"] for case in _FIXTURE["cases"]])
def test_the_builder_renders_the_shared_fixture(case: dict[str, object]) -> None:
    builder = _build(case)
    assert builder.to_dockerfile() == case["dockerfile"]
    source, dockerfile = builder._request_source()
    assert ({"source": source} if source is not None else {"dockerfile": dockerfile}) == case["request"]


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
