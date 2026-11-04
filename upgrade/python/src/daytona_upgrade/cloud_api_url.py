# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Cloud URL identity without changing the case-sensitive API path."""

from __future__ import annotations

import re


def cloud_url_identity(url: str) -> str:
    """Normalize scheme/authority case and remove at most one trailing slash."""
    return re.sub(
        r"^(https?://[^/?#]+)",
        lambda match: match[0].lower(),
        url,
        flags=re.IGNORECASE | re.ASCII,
    ).removesuffix("/")
