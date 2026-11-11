# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Every sandbox wait target is sent as a status of the contract's ``SandboxStatus``.

The API answers a ``wait`` / ``wait_exact`` query naming anything else with
400, so the mapping from the SDK's wait states must stay inside the enum.
"""

from __future__ import annotations

from typing import get_args

import pytest

from daytona.v2._gen.api.models.sandbox_status import SandboxStatus
from daytona.v2.errors import BadRequestError
from daytona.v2.sandbox import _wait_query_state, _wait_states
from daytona.v2.types import SandboxWaitState

_CONTRACT_STATUSES = {member.value for member in SandboxStatus} - {"unknown_default_open_api"}

_EXPECTED = {
    "queued": "queued",
    "starting": "booting",
    "started": "running",
    "pausing": "pausing",
    "paused": "paused",
    "stopping": "stopping",
    "stopped": "stopped",
    "destroying": "deleting",
    "destroyed": "deleted",
}


@pytest.mark.parametrize("state", get_args(SandboxWaitState))
def test_wait_state_is_sent_as_a_contract_status(state: SandboxWaitState) -> None:
    assert _wait_query_state(state) == _EXPECTED[state]
    assert _EXPECTED[state] in _CONTRACT_STATUSES


def test_every_wait_state_has_an_expected_status() -> None:
    assert set(_EXPECTED) == set(get_args(SandboxWaitState))


def test_contract_declares_the_thirteen_statuses() -> None:
    assert len(_CONTRACT_STATUSES) == 13


@pytest.mark.parametrize("until", ["running", "deleted", "creating", "error", "unknown", "STARTED"])
def test_a_non_wait_state_is_refused_locally(until: str) -> None:
    with pytest.raises(BadRequestError) as caught:
        _wait_states(until)
    assert (caught.value.status_code, caught.value.code, caught.value.source) == (400, "BAD_REQUEST", "SDK")
