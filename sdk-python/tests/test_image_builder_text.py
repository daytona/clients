# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""ENV and local files share BuildKit's parser-directive semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona.v2 import BadRequestError, ImageBuilder

_ESCAPE_CASES = json.loads((Path(__file__).parent / "fixtures/dockerfile-escape.json").read_text("utf-8"))[
    "cases"
]


@pytest.mark.parametrize("case", _ESCAPE_CASES, ids=lambda case: repr(case["dockerfile"]))
def test_env_literal_support_when_buildkit_reads_escape_directives(case: dict[str, str]) -> None:
    # Given: directives whose effective escape character was measured in BuildKit.
    builder = ImageBuilder.from_dockerfile(case["dockerfile"])
    # When: a literal env value is appended, then the effective escape decides support.
    if case["escape"] == "\\":
        assert builder.env("A", "$PATH").to_dockerfile().endswith('ENV A="\\$PATH"\n')
    else:
        with pytest.raises(BadRequestError) as raised:
            builder.env("A", "$PATH")
        assert str(raised.value) == (
            "ImageBuilder.env cannot keep values literal under a Dockerfile escape directive other than a backslash"
        )
