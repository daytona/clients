# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Class range endpoints use parseEscape, not shorthand sets."""

import pytest

from daytona.v2._dockerignore import Matcher
from daytona.v2._dockerignore_error import PatternError
from daytona.v2.errors import BadRequestError


@pytest.mark.parametrize("pattern", [r"x[ -\pL\pL]*", r"[z\101-\pL]x"])
def test_class_shorthand_is_refused_when_used_as_range_endpoint(pattern: str) -> None:
    # Given a pattern accepted by filepath.Match but rejected by regexp.Compile.
    matcher = Matcher((pattern,))
    # When evaluated, then a shorthand cannot serve as a range endpoint.
    with pytest.raises(PatternError, match="invalid regular expression"):
        matcher.excluded("x")


@pytest.mark.parametrize("pattern", ["x[a-😀-|]", "x[𐐀-😀-a]", r"x[\9-😀-|]"])
def test_malformed_astral_range_is_refused_when_initializing_matcher(pattern: str) -> None:
    # Given a malformed range following an astral Unicode code point.
    # When constructed, then filepath.Match rejects syntax before any evaluation.
    with pytest.raises(BadRequestError, match="syntax error in pattern"):
        Matcher((pattern,))
