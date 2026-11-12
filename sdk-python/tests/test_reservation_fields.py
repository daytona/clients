# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from dataclasses import asdict
from datetime import datetime
from typing import Final, Literal

import httpx
import pytest
from typing_extensions import assert_never

from daytona.v2._gen.api import MacReservation as WireReservation
from daytona.v2._transport import AsyncTransport
from daytona.v2.services import AsyncReservationsService

# Full API response fixture, including optional lifecycle and billing fields.
WIRE: Final = {
    "id": "region-a:reservation-1",
    "org_id": "00000000-0000-0000-0000-000000000001",
    "project_id": "00000000-0000-0000-0000-000000000002",
    "region_id": "region-a",
    "status": "released",
    "origin": "auto",
    "auto_renew": False,
    "slots_used": 2,
    "model": "m4-10c-16g",
    "capacity": {"cores": 10, "memory_mib": 16384, "disk_mib": 235520},
    "resources_used": {"cores": 5, "memory_mib": 8192, "disk_mib": 117760},
    "period_n": 3,
    "period_seconds": 86400,
    "created_at": "2026-10-20T00:00:00Z",
    "pending_mac_started_at": "2026-10-20T00:01:00Z",
    "activated_at": "2026-10-20T00:02:00Z",
    "period_started_at": "2026-10-22T00:02:00Z",
    "period_end": "2026-10-23T00:02:00Z",
    "reissued_at": "2026-10-21T00:00:00Z",
    "ended_at": "2026-10-23T00:02:01Z",
    "end_reason": "expired",
    "renewal_pending": True,
    "renewal_error": "budget held",
}

Operation = Literal["get", "create", "renew", "update", "list", "create_bulk"]


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["get", "create", "renew", "update", "list", "create_bulk"])
async def test_every_reservation_field_when_returned_by_service(operation: Operation) -> None:
    # Given: real wire JSON parsed through the SDK's HTTP transport.
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/reservations/bulk":
            return httpx.Response(200, json={"requested": 1, "reserved": 1, "reservations": [WIRE]})
        if request.method == "GET" and request.url.path == "/reservations":
            return httpx.Response(200, json={"reservations": [WIRE], "next_cursor": None})
        return httpx.Response(200, json=WIRE)

    expected = {
        **WIRE,
        **{
            field: datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            for field, timestamp in (
                ("created_at", "2026-10-20T00:00:00Z"),
                ("pending_mac_started_at", "2026-10-20T00:01:00Z"),
                ("activated_at", "2026-10-20T00:02:00Z"),
                ("period_started_at", "2026-10-22T00:02:00Z"),
                ("period_end", "2026-10-23T00:02:00Z"),
                ("reissued_at", "2026-10-21T00:00:00Z"),
                ("ended_at", "2026-10-23T00:02:01Z"),
            )
        },
        "raw_status": "released",
        "raw_origin": "auto",
        "raw_end_reason": "expired",
    }
    # The public dataclass names the region `region`; the wire calls it `region_id`.
    expected["region"] = expected.pop("region_id")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        service = AsyncReservationsService(AsyncTransport("https://api.test", client=http))
        # When
        match operation:
            case "get":
                result = await service.get("region-a:reservation-1")
            case "create":
                result = await service.create(region="region-a")
            case "renew":
                result = await service.renew("region-a:reservation-1")
            case "update":
                result = await service.update("region-a:reservation-1", auto_renew=False)
            case "list":
                result = (await service.list()).items[0]
            case "create_bulk":
                result = (await service.create_bulk(region="region-a", count=1)).reservations[0]
            case unreachable:
                assert_never(unreachable)
    # Then: every generated reservation field is present in the public dataclass.
    assert set(WIRE) == set(WireReservation.model_fields) - {"additional_properties"}
    actual = asdict(result)
    actual["capacity"] = asdict(result.capacity) if result.capacity is not None else None
    actual["resources_used"] = asdict(result.resources_used)
    assert actual == expected


@pytest.mark.anyio
@pytest.mark.parametrize("omit", [False, True])
async def test_optional_reservation_fields_when_null_or_omitted(omit: bool) -> None:
    # Given
    optional_fields = (
        "activated_at",
        "capacity",
        "model",
        "end_reason",
        "ended_at",
        "pending_mac_started_at",
        "period_end",
        "period_started_at",
        "reissued_at",
        "renewal_error",
    )
    payload = {key: value for key, value in WIRE.items() if key not in optional_fields}
    if not omit:
        payload.update(dict.fromkeys(optional_fields))
    payload.update(auto_renew=True, slots_used=0)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as http:
        service = AsyncReservationsService(AsyncTransport("https://api.test", client=http))
        # When
        result = await service.get("region-a:reservation-1")
    # Then
    values = asdict(result)
    assert {key: values[key] for key in optional_fields} == dict.fromkeys(optional_fields)
    assert (result.raw_end_reason, result.auto_renew, result.slots_used) == (None, True, 0)


@pytest.mark.anyio
async def test_raw_reservation_enums_when_wire_values_are_unknown() -> None:
    # Given
    payload = {**WIRE, "status": "future_status", "origin": "future_origin", "end_reason": "future_reason"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as http:
        service = AsyncReservationsService(AsyncTransport("https://api.test", client=http))
        # When
        result = await service.get("region-a:reservation-1")
    # Then
    assert (result.status, result.raw_status) == ("unknown", "future_status")
    assert (result.origin, result.raw_origin) == ("unknown", "future_origin")
    assert (result.end_reason, result.raw_end_reason) == ("unknown", "future_reason")


_RESERVATION_TIMESTAMPS = (
    "created_at",
    "activated_at",
    "pending_mac_started_at",
    "period_started_at",
    "period_end",
    "reissued_at",
    "ended_at",
)


@pytest.mark.anyio
@pytest.mark.parametrize("stamp", ["nope", "2026-10-20", "2026-10-20T00:00:00"])
@pytest.mark.parametrize("field", _RESERVATION_TIMESTAMPS)
async def test_a_reservation_timestamp_that_is_not_rfc_3339_is_refused(field: str, stamp: str) -> None:
    # The contract's date-time fields are RFC 3339 timestamps; a date, or one
    # without an offset, is refused naming the field (twin in
    # reservation-fields.test.ts).
    from daytona.v2 import BadRequestError

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**WIRE, field: stamp})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        service = AsyncReservationsService(AsyncTransport("https://api.test", client=http))
        with pytest.raises(BadRequestError, match=f"has a {field} that is not an RFC 3339 timestamp"):
            await service.get("region-a:reservation-1")
