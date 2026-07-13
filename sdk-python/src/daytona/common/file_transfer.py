# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import urllib.parse

from collections.abc import Callable, Mapping
from typing import Any

from python_multipart.multipart import MultipartParser, MultipartState, parse_options_header

from daytona_toolbox_api_client import FilesDownloadRequest

from .errors import DaytonaError


def _parse_content_disposition_parameters(header: str) -> dict[str, str]:
    """Parse Content-Disposition parameters without mangling file paths.

    Each part of a bulk-download response identifies its file by echoing the
    requested path in the Content-Disposition header, and we match parts back
    to requests by exact string comparison — so the path must survive parsing
    byte-for-byte. python_multipart's parse_options_header cannot guarantee
    that: it strips a backslash-containing filename down to its basename
    (an IE-era upload compatibility behavior), turning a requested
    ``C:\\Windows\\Temp\\x`` into ``x`` and breaking the lookup.

    This parser preserves the parameter values instead: ``filename*`` remains
    RFC 5987 percent-encoded for the caller to decode as the lossless form, and
    quoted ``filename`` has quoted-pair escapes removed without path
    normalization.
    """
    parameters: dict[str, str] = {}
    position = header.find(";")
    length = len(header)

    while position >= 0 and position < length:
        position += 1
        while position < length and header[position].isspace():
            position += 1

        name_start = position
        while position < length and header[position] not in "=;":
            position += 1
        if position >= length or header[position] != "=":
            position = header.find(";", position)
            continue

        name = header[name_start:position].strip().lower()
        position += 1
        while position < length and header[position].isspace():
            position += 1

        if position < length and header[position] == '"':
            position += 1
            value_chars: list[str] = []
            while position < length:
                char = header[position]
                if char == "\\" and position + 1 < length:
                    position += 1
                    value_chars.append(header[position])
                elif char == '"':
                    position += 1
                    break
                else:
                    value_chars.append(char)
                position += 1
            value = "".join(value_chars)
            while position < length and header[position] != ";":
                position += 1
        else:
            value_start = position
            while position < length and header[position] != ";":
                position += 1
            value = header[value_start:position].strip()

        if name and name not in parameters:
            parameters[name] = value

    return parameters


def parse_content_disposition(header: str) -> tuple[str | None, str | None]:
    """Return the multipart field name and decoded filename label."""
    parameters = _parse_content_disposition_parameters(header)
    name = parameters.get("name")
    extended_filename = parameters.get("filename*")
    if extended_filename is None:
        return name, parameters.get("filename")

    extended_parts = extended_filename.split("'", 2)
    if len(extended_parts) != 3 or extended_parts[0].lower() != "utf-8":
        raise DaytonaError("Invalid Content-Disposition filename* parameter")

    encoded_filename = extended_parts[2]
    for index, char in enumerate(encoded_filename):
        if char == "%" and (
            index + 2 >= len(encoded_filename)
            or encoded_filename[index + 1] not in "0123456789abcdefABCDEF"
            or encoded_filename[index + 2] not in "0123456789abcdefABCDEF"
        ):
            raise DaytonaError("Invalid Content-Disposition filename* parameter")

    try:
        filename = urllib.parse.unquote_to_bytes(encoded_filename).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DaytonaError("Invalid Content-Disposition filename* parameter") from exc
    return name, filename


def serialize_download_request(api_client: Any, remote_path: str) -> tuple[str, str, dict[str, str], Any]:
    method, url, headers, body, *_ = api_client._download_files_serialize(
        download_files=FilesDownloadRequest(paths=[remote_path]),
        _request_auth=None,
        _content_type=None,
        _headers=None,
        _host_index=None,
    )
    return method, url, headers, body


def parse_content_type_boundary(headers: Mapping[str, str]) -> bytes:
    content_type_raw, options = parse_options_header(headers.get("Content-Type", ""))
    if not (content_type_raw == b"multipart/form-data" and b"boundary" in options):
        raise DaytonaError(f"Unexpected Content-Type: {content_type_raw!r}")
    return options[b"boundary"]


def create_multipart_parser(
    boundary: bytes,
    on_part_begin: Callable[[], None],
    on_header_field: Callable[[bytes, int, int], None],
    on_header_value: Callable[[bytes, int, int], None],
    on_header_end: Callable[[], None],
    on_headers_finished: Callable[[], None],
    on_part_data: Callable[[bytes, int, int], None],
    on_part_end: Callable[[], None],
) -> MultipartParser:
    return MultipartParser(
        boundary,
        callbacks={
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )


def raise_if_multipart_truncated(parser: MultipartParser, remote_path: str) -> None:
    """Raise if the multipart stream ended before the closing boundary.

    python_multipart can silently drop boundary look-ahead bytes at finalize().
    Call after parser.finalize() so short downloads become retryable errors.
    """
    if parser.state != MultipartState.END:
        msg = (
            f"Truncated multipart response for {remote_path!r}: "
            f"closing boundary not received (parser state={parser.state.name})"
        )
        raise DaytonaError(msg)
