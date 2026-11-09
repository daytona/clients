# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Final, Sequence

import httpx

from . import _codes as codes
from ._core import finite_number, parse_json_response
from .errors import create_error
from .types import JsonObject, JsonValue, NetworkPolicy, NetworkPolicyInfo, NetworkPolicyInput

_INPUT_FIELDS: Final[frozenset[str]] = frozenset({"block_all", "allow_cidrs", "allow_domains"})


def serialize_network_policy_input(value: NetworkPolicyInput) -> JsonObject:
    if not isinstance(value, dict):
        raise create_error("network_policy must be an object", 400, "BAD_REQUEST", "SDK")
    unknown = [str(name) for name in value if name not in _INPUT_FIELDS]
    if unknown:
        raise create_error(
            f"network_policy contains unknown fields: {', '.join(unknown)}",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    body: JsonObject = {}
    block_all: bool | None = None
    if "block_all" in value:
        raw_block_all = value["block_all"]
        if raw_block_all is not None and not isinstance(raw_block_all, bool):
            raise create_error("network_policy.block_all must be a boolean", 400, "BAD_REQUEST", "SDK")
        block_all = raw_block_all
    if "allow_cidrs" in value:
        body["allow_cidrs"] = _request_string_list(
            value["allow_cidrs"],
            field_name="network_policy.allow_cidrs",
        )
    allow_domains: list[str] | None = None
    allow_domains_present = "allow_domains" in value
    if "allow_domains" in value:
        raw_allow_domains = value["allow_domains"]
        if raw_allow_domains is None:
            body["allow_domains"] = None
        else:
            allow_domains = _request_string_list(
                raw_allow_domains,
                field_name="network_policy.allow_domains",
            )
            body["allow_domains"] = allow_domains
    if allow_domains_present and allow_domains is not None and block_all is False:
        # Same code as the API's own 400 (the server catalog is upper case),
        # so callers can key on one value whichever side refused.
        raise create_error(
            "network_policy allow_domains requires block_all to be true or omitted",
            400,
            codes.NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL,
            "SDK",
        )
    if block_all is not None:
        body["block_all"] = block_all
    return body


def parse_network_policy_response(response: httpx.Response) -> NetworkPolicyInfo:
    payload = _expect_response_object(
        parse_json_response(response.content, what="network policy response"),
        field_name="network policy response",
    )
    etag = response.headers.get("etag")
    if not isinstance(etag, str) or not etag:
        raise create_error("network_policy response is missing the ETag header", 400, "BAD_REQUEST", "SDK")
    desired_revision = _required_int(payload.get("desired_revision"), field_name="desired_revision")
    return NetworkPolicyInfo(
        desired=_optional_policy(payload.get("desired"), field_name="desired"),
        desired_revision=desired_revision,
        etag=etag,
        http_status=response.status_code,
        applied=_optional_policy(payload.get("applied"), field_name="applied"),
        applied_revision=_optional_int(payload.get("applied_revision"), field_name="applied_revision"),
        applied_start_id=_optional_str(payload.get("applied_start_id")),
        status=_optional_str(payload.get("status")),
        error=_optional_str(payload.get("error")),
    )


def _optional_policy(value: JsonValue, *, field_name: str) -> NetworkPolicy | None:
    if value is None:
        return None
    payload = _expect_response_object(value, field_name=field_name)
    return NetworkPolicy(
        version=_required_int(payload.get("version"), field_name=f"{field_name}.version"),
        block_all=_required_bool(payload.get("block_all"), field_name=f"{field_name}.block_all"),
        allow_cidrs=tuple(
            _response_string_list(
                payload.get("allow_cidrs"),
                field_name=f"{field_name}.allow_cidrs",
            )
        ),
        allow_domains=_tuple_or_none(
            _optional_response_string_list(
                payload.get("allow_domains"),
                field_name=f"{field_name}.allow_domains",
            )
        ),
    )


def _tuple_or_none(values: list[str] | None) -> tuple[str, ...] | None:
    return None if values is None else tuple(values)


def _expect_response_object(value: JsonValue, *, field_name: str) -> JsonObject:
    if isinstance(value, dict):
        return value
    raise create_error(f"{field_name} must be an object", 400, "BAD_REQUEST", "SDK")


def _request_string_list(value: JsonValue, *, field_name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise create_error(f"{field_name} must be an array of strings", 400, "BAD_REQUEST", "SDK")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise create_error(f"{field_name} must be an array of strings", 400, "BAD_REQUEST", "SDK")
        items.append(item)
    return items


def _response_string_list(value: JsonValue, *, field_name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise create_error(f"{field_name} must be an array of strings", 400, "BAD_REQUEST", "SDK")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise create_error(f"{field_name} must be an array of strings", 400, "BAD_REQUEST", "SDK")
        items.append(item)
    return items


def _optional_response_string_list(value: JsonValue, *, field_name: str) -> list[str] | None:
    if value is None:
        return None
    return _response_string_list(value, field_name=field_name)


def _required_bool(value: JsonValue, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise create_error(f"{field_name} must be a boolean", 400, "BAD_REQUEST", "SDK")


def _required_int(value: JsonValue, *, field_name: str) -> int:
    # An integer too large for a double is refused, as in TypeScript, whose
    # JSON.parse reads it as Infinity, which its integer check then refuses.
    if isinstance(value, int) and finite_number(value):
        return value
    raise create_error(f"{field_name} must be an integer", 400, "BAD_REQUEST", "SDK")


def _optional_int(value: JsonValue, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, field_name=field_name)


def _optional_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None
