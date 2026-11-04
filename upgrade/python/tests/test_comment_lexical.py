# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Suggestion comments never consume identically spelled runtime string data."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import Mode, SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def transform(source: str, mode: Mode) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, mode),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "literal",
    [
        '"""hello\n# daytona-v2: this is data, not a comment\nworld"""',
        "r'''hello\n    # daytona-v2: data\nworld'''",
        'b"""hello\n\t# daytona-v2: data\nworld"""',
        'f"""hello\n# daytona-v2: data {1}\nworld"""',
        '"hello\\\n# daytona-v2: data"',
        '"""hello\vdata\u2028more\n# daytona-v2: data\nworld"""',
    ],
)
def test_removal_preserves_strings_when_tool_comments_surround_them(
    literal: str, ending: str
) -> None:
    # Given the original data-loss reproduction and real standalone/inline comments.
    data = ("message = " + literal + "\n").replace("\n", ending)
    kept = data + 'value = "kept"  # daytona-v2: inline user note' + ending
    source = "# daytona-v2: stale" + ending + kept + "\t# daytona-v2: stale"
    # When removing tool suggestions.
    result = transform(source, "remove-suggestions")
    # Then only standalone COMMENT tokens disappear, byte-exactly.
    assert result.source.encode() == kept.encode()


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_rerun_replaces_comments_when_string_contains_tool_tags(ending: str) -> None:
    # Given old advice next to string data and a proven v0 call.
    original = (
        'message = """hello\n# daytona-v2: runtime data\nworld"""\n'
        "from daytona import Daytona\napi = Daytona()\napi.v0.create()\n"
    ).replace("\n", ending)
    stale = "# daytona-v2: obsolete" + ending + original
    # When regenerating suggestions, rerunning, and removing the generated advice.
    fresh = transform(stale, "suggest-v2").source
    rerun = transform(fresh, "suggest-v2").source
    restored = transform(rerun, "remove-suggestions").source
    # Then replacement is idempotent and string bytes survive the round trip.
    assert rerun == fresh
    assert restored.encode() == original.encode()
    assert "# daytona-v2: obsolete" not in fresh
