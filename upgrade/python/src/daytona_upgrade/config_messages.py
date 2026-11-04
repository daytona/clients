# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""URL refusal guidance shared by config preflight and the real CLI report."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .models import Item

UNRESOLVED_CONFIG: Final = "cannot verify the API URL of this DaytonaConfig; if it targets Daytona Cloud, rerun with --drop-api-url"
CUSTOM_URL: Final = (
    "SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x."
)
SHARED_URL_WRITE: Final = "SDK 2.x refuses URL fields in the client config; this config is shared or its ownership is unresolved. Give the client a URL-free config and keep this URL write for other readers."
UNRESOLVED_URL_FIELDS: Final = "SDK 2.x refuses URL fields in the client config; this config is shared or its ownership is unresolved. Give the client a URL-free config."
ENV_CUSTOM_URL: Final = (
    CUSTOM_URL + " If it points at Daytona Cloud, rerun with --drop-api-url."
)


def url_refusal(items: Sequence[Item]) -> tuple[str, list[str]]:
    """Prefer known-custom refusal over assertion guidance on mixed evidence."""
    if any(
        item["kind"] == "custom-api-url" and item["message"] == CUSTOM_URL
        for item in items
    ):
        return CUSTOM_URL + " No files were written.", []
    if any(
        item["kind"] == "custom-api-url" and item["message"] == ENV_CUSTOM_URL
        for item in items
    ):
        return ENV_CUSTOM_URL + " No files were written.", []
    return (
        "Cannot verify that every DaytonaConfig targets Daytona Cloud. If they do, rerun with --drop-api-url; for a custom API URL, stay on SDK 0.x. No files were written.",
        ["rerun with --drop-api-url if every DaytonaConfig targets Daytona Cloud"],
    )
