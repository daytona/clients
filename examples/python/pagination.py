#!/usr/bin/env python3
"""pagination — list images with `iter()` or page by page with cursors.

Run (from examples):
    DAYTONA_API_KEY=dtn_... python python/pagination.py
"""

from __future__ import annotations

import sys

from _common import client, info, section


def main() -> int:
    daytona = client()

    section("images.iter — lazy all pages")
    for image in daytona.images.iter(limit=10):
        info(f"   - {image.name} ({image.status})")

    section("images.list — walk pages until exhausted")
    limit = 10
    page = 1
    cursor = None
    seen = 0
    while True:
        result = daytona.images.list(cursor=cursor, limit=limit)
        items = result.items
        seen += len(items)
        info(f"page {page}: {len(items)} item(s)")
        for image in items:
            info(f"   - {image.name} ({image.status or 'unknown'})")
        cursor = result.next_cursor
        if not cursor or not items:
            break
        page += 1

    info(f"walked {seen} image(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
