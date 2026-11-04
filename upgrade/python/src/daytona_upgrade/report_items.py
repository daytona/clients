# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Stable report de-duplication using canonical JSON keys."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from .models import Item


def unique_items(items: Iterable[Item]) -> Iterator[Item]:
    """Keep the first equal report item, including nested suggestion metadata."""
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            yield item
