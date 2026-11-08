#!/usr/bin/env python3
"""images — build a reusable image, then boot sandboxes from it.

An image is a named, reusable environment built ahead of sandbox creation.
Two source variants are supported:

  * a registry image ref:   ``daytona.images.pull(image, name=...)``
  * an inline Dockerfile:   ``daytona.images.build(ImageBuilder.from_dockerfile(...), name=...)``

Use ``images.follow_build_logs()`` to read build output. This example builds
an image from a Dockerfile, then boots a sandbox from it.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/snapshots.py
"""

from __future__ import annotations

import sys
import time

from _common import IMAGE, RUN, client, failed, info, passed, section

from daytona import DaytonaV2Client
from daytona.v2 import ImageBuilder


def wait_for_image_ready(daytona: DaytonaV2Client, image_name: str) -> None:
    for _ in range(120):
        cursor = None
        entry = None
        for _page in range(50):
            page = daytona.images.list(cursor=cursor)
            entry = next((item for item in page.items if item.name == image_name), None)
            if entry or not page.next_cursor or page.next_cursor == cursor:
                break
            cursor = page.next_cursor
        if entry and entry.status == "ready":
            return
        if entry and entry.status == "failed":
            raise RuntimeError(f"image {image_name} failed")
        time.sleep(1)
    raise RuntimeError(f"image {image_name} did not become ready")


def main() -> int:
    daytona = client()
    image_name = f"example-img-{RUN}"
    ok = True
    created = False

    try:
        section("images.build (build from a Dockerfile)")
        dockerfile = f"FROM {IMAGE}\nRUN pip install --no-cache-dir requests\n"
        snap = daytona.images.build(ImageBuilder.from_dockerfile(dockerfile), name=image_name)
        created = True
        info(f"image status={snap.status} id={snap.id}")
        if snap.id:
            passed("image build accepted")
        else:
            failed("image build was not accepted")
            ok = False

        section("images.list")
        page = daytona.images.list()
        contains = any(s.name == image_name for s in page.items)
        info(f"contains ours={contains}")
        wait_for_image_ready(daytona, image_name)

        section("create a sandbox FROM the registered image")
        sandbox = daytona.create(image_name, name=f"from-img-{RUN}", wait_seconds=200)
        try:
            py = sandbox.process.run("python --version")
            info(f"sandbox python: {py.output.strip()}")
            if py.exit_code == 0:
                passed("sandbox booted from the image and runs")
            else:
                failed(f"sandbox from image failed to run (exit={py.exit_code})")
                ok = False
        finally:
            daytona.delete(sandbox)
            info(f"deleted sandbox {sandbox.id}")
    finally:
        if created and snap.id:
            section("cleanup image")
            daytona.images.delete(snap.id)
            info(f"deleted image {snap.id}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
