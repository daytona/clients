# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, Optional, TypeGuard
from urllib.parse import quote

from ._core import (
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    TransportPayload,
    _expect_model_from_dict,
    enum_str,
    json_object_from_mapping,
)
from ._gen.toolbox import PullVolumeRequest, PullVolumeResponse
from ._gen.toolbox import PullVolumeStatus as PullVolumeStatusEnum
from ._transport import AsyncTransport
from .errors import create_error
from .types import JsonObject

PullVolumeStatus = Literal["pulled", "unchanged", "pending", "unknown"]
_PULL_VOLUME_STATUS_VALUES = tuple(
    str(member.value) for member in PullVolumeStatusEnum if member.name != "UNKNOWN_DEFAULT_OPEN_API"
)


def _is_pull_volume_status_known(value: str) -> TypeGuard[Literal["pulled", "unchanged", "pending"]]:
    return value in _PULL_VOLUME_STATUS_VALUES


def _pull_volume_status_or_unknown(value: str) -> PullVolumeStatus:
    return value if _is_pull_volume_status_known(value) else "unknown"


@dataclass(frozen=True, kw_only=True)
class PullVolumeResult:
    request_id: str
    status: PullVolumeStatus
    raw_status: str
    manifest_id: Optional[str] = None


def pull_volume_spec(volume_id: str, *, request_id: str, timeout: Optional[float] = None) -> RequestSpec:
    """Sans-I/O validation and rendering shared by both transports."""
    for name, value in (("volume_id", volume_id), ("request_id", request_id)):
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
            raise create_error(
                f"{name} must contain 1–128 ASCII letters, digits, underscores or hyphens",
                400,
                "BAD_REQUEST",
                "SDK",
            )
    body: JsonObject = {"requestId": request_id}
    if timeout is not None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 <= timeout <= 30
            or not math.isfinite(timeout)
        ):
            raise create_error("timeout must be finite and in 0..30 seconds", 400, "BAD_REQUEST", "SDK")
        # Round down to whole wire milliseconds, never exceeding the caller's wait,
        # but keep any positive wait positive: zero means check, not retry.
        body["waitMs"] = max(int(timeout * 1000), 1) if timeout > 0 else 0
    request = PullVolumeRequest.model_validate(body)
    return RequestSpec(
        "POST",
        f"/volumes/{quote(volume_id, safe='')}/pull",
        json_body=json_object_from_mapping(
            request.model_dump(mode="json", by_alias=True, exclude_unset=True)
        ),
        # Leave time to receive pending after the server wait, including a zero wait.
        timeout=(30 if timeout is None else timeout) + WAIT_READ_SLACK_SECONDS,
    )


def parse_pull_volume(value: TransportPayload) -> PullVolumeResult:
    response = _expect_model_from_dict(
        value, PullVolumeResponse, "volume pull result", PullVolumeResponse.from_dict
    )
    raw_status = enum_str(response.status)
    return PullVolumeResult(
        request_id=response.request_id,
        status=_pull_volume_status_or_unknown(raw_status),
        raw_status=raw_status,
        manifest_id=response.manifest_id,
    )


class AsyncSandboxVolumesService:
    """Pull attached volumes, separate from the root platform volume service."""

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def pull(
        self, volume_id: str, *, request_id: str, timeout: Optional[float] = None
    ) -> PullVolumeResult:
        """Submit/check once; timeout is a server wait in seconds (0–30, default 30).

        Zero does not wait: it submits a new request_id, or returns a known one's
        last outcome without retrying it; a timeout above zero retries pending,
        unavailable or rate-limited work. Expiry does not cancel work. Neither
        retries nor identities are automatic.
        """
        spec = pull_volume_spec(volume_id, request_id=request_id, timeout=timeout)
        return parse_pull_volume(await self._transport.send(spec))
