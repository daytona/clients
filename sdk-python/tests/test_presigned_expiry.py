# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

import pytest

from daytona.v2 import SIGNED_FILE_MAX_TTL_SECONDS
from daytona.v2.errors import BadRequestError
from daytona.v2.filesystem import _assert_expires_in_seconds, _expires_unix_seconds

EXPIRES_MESSAGE = "expires_in_seconds must be an integer between 1 and 604800 (7 days)"


def test_ceiling_matches_the_server_default_of_seven_days() -> None:
    assert SIGNED_FILE_MAX_TTL_SECONDS == 7 * 24 * 60 * 60 == 604_800


@pytest.mark.parametrize("seconds", [1, 3600, SIGNED_FILE_MAX_TTL_SECONDS])
def test_in_range_expiry_adds_to_the_whole_second_now(seconds: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daytona.v2.filesystem.time.time", lambda: 1_781_230_967.75)

    expires = _expires_unix_seconds(seconds)

    assert expires == 1_781_230_967 + seconds
    assert type(expires) is int


@pytest.mark.parametrize(
    "seconds",
    [
        0,  # links require an expiry
        -1,
        SIGNED_FILE_MAX_TTL_SECONDS + 1,
        2**63,
        3600.0,  # integer-valued floats are not integers in Python
        1.5,
        float("nan"),
        float("inf"),
        True,
        False,
        "3600",
        None,
    ],
)
def test_out_of_range_or_non_integer_expiry_is_a_sdk_bad_request(seconds: object) -> None:
    with pytest.raises(BadRequestError) as info:
        _assert_expires_in_seconds(seconds)  # type: ignore[arg-type]

    assert str(info.value) == EXPIRES_MESSAGE
    assert info.value.status_code == 400
    assert info.value.code == "BAD_REQUEST"
    assert info.value.source == "SDK"
