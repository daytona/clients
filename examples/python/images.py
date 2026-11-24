#!/usr/bin/env python3
"""images — build a reusable image, then boot a sandbox from it.

An image is a named, reusable environment built ahead of sandbox creation.
Two source variants are supported:

  * a registry image ref:   ``daytona.images.pull(image, name=...)``
  * an inline Dockerfile:   ``daytona.images.build(ImageBuilder.from_dockerfile(...), name=...)``

Use ``images.follow_build_logs()`` to read build output. This example builds
an image from a Dockerfile, waits for it by reading the image itself
(``images.get(id, wait="ready")``), boots a sandbox from it, and deletes both
the sandbox and the image the run built, also when a step fails.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/images.py
"""

from __future__ import annotations

import sys

from _common import IMAGE, IMAGE_WAIT_SECONDS, RUN, client, failed, info, passed, require_started, section

from daytona import DaytonaV2Client
from daytona.v2 import Image, ImageBuilder, RequestTimeoutError


def wait_for_build(daytona: DaytonaV2Client, image: Image) -> Image:
    """One budgeted wait on the image itself: it returns once the build settles."""
    try:
        settled: Image = daytona.images.get(image.id, wait="ready", timeout=IMAGE_WAIT_SECONDS)
    except RequestTimeoutError:
        raise RuntimeError(
            f"image {image.name or image.id} did not settle within {IMAGE_WAIT_SECONDS} s"
        ) from None
    return settled


def main() -> int:
    daytona = client()
    image_name = f"example-img-{RUN}"

    section("images.build (build from a Dockerfile)")
    dockerfile = f"FROM {IMAGE}\nRUN pip install --no-cache-dir requests\n"
    built = daytona.images.build(ImageBuilder.from_dockerfile(dockerfile), name=image_name)
    info(f"image status={built.status} id={built.id}")
    try:
        section("images.get (wait for the build)")
        image = wait_for_build(daytona, built)
        if image.status != "ready":
            detail = f": {image.error}" if image.error else ""
            failed(f"image {image_name} ended {image.status}{detail}")
            return 1
        passed("image is ready")

        section("create a sandbox from the built image")
        sandbox = daytona.create(image_name, name=f"from-img-{RUN}", wait_seconds=200)
        try:
            if not require_started(sandbox):
                return 1
            py = sandbox.process.run("python --version")
            info(f"sandbox python: {py.output.strip()}")
            if py.exit_code == 0:
                passed("sandbox booted from the image and runs")
                return 0
            failed(f"sandbox from image failed to run (exit={py.exit_code})")
            return 1
        finally:
            daytona.delete(sandbox)
            info(f"deleted sandbox {sandbox.id}")
    finally:
        section("cleanup image")
        daytona.images.delete(built.id)
        info(f"deleted image {built.id}")


if __name__ == "__main__":
    sys.exit(main())
