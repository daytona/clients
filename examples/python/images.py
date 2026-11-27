#!/usr/bin/env python3
"""images — build a reusable image, then boot a sandbox from it.

An image is a named, reusable environment built ahead of sandbox creation.
Two source variants are supported:

  * a registry image ref:   ``daytona.images.pull(image, name=...)``
  * an inline Dockerfile:   ``daytona.images.build(ImageBuilder.from_dockerfile(...), name=...)``

Use ``images.follow_build_logs()`` to read build output. This example builds
an image from a Dockerfile, waits for it by reading the image itself
(``images.get(id, wait="ready")``), boots a sandbox from it, and deletes both
the sandbox and the image the run built, also when a step fails. A build whose
submission succeeded but whose reply was lost is found by its unique name, and
an image delete refused while the sandbox still releases it (409) is retried
for a short while.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/images.py
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

from _common import IMAGE, IMAGE_WAIT_SECONDS, RUN, client, failed, info, passed, require_started, section

from daytona import DaytonaV2Client
from daytona.v2 import ConflictError, Image, ImageBuilder, NotFoundError, RequestTimeoutError

DELETE_ATTEMPTS = 10
DELETE_RETRY_SECONDS = float(os.environ.get("EXAMPLE_RETRY_DELAY_SECONDS", "3"))


def wait_for_build(daytona: DaytonaV2Client, image: Image) -> Image:
    """One budgeted wait on the image itself: it returns once the build settles."""
    try:
        settled: Image = daytona.images.get(image.id, wait="ready", timeout=IMAGE_WAIT_SECONDS)
    except RequestTimeoutError:
        raise RuntimeError(
            f"image {image.name or image.id} did not settle within {IMAGE_WAIT_SECONDS} s"
        ) from None
    return settled


def built_image_id(daytona: DaytonaV2Client, built: Optional[Image], image_name: str) -> Optional[str]:
    """The id of the image this run built: the build's reply, else a lookup by its unique name."""
    if built is not None:
        return built.id
    try:
        found: Image = daytona.images.get(image_name)
    except NotFoundError:
        return None
    return found.id


def delete_image(daytona: DaytonaV2Client, image_id: str) -> None:
    """Delete the image, retrying while the API still holds it for a deleted sandbox (409)."""
    for attempt in range(1, DELETE_ATTEMPTS + 1):
        try:
            daytona.images.delete(image_id)
            return
        except ConflictError:
            if attempt >= DELETE_ATTEMPTS:
                raise
            info(f"image {image_id} still in use; retrying the delete")
            time.sleep(DELETE_RETRY_SECONDS)


def main() -> int:
    daytona = client()
    image_name = f"example-img-{RUN}"
    built: Optional[Image] = None

    try:
        section("images.build (build from a Dockerfile)")
        dockerfile = f"FROM {IMAGE}\nRUN pip install --no-cache-dir requests\n"
        built = daytona.images.build(ImageBuilder.from_dockerfile(dockerfile), name=image_name)
        info(f"image status={built.status} id={built.id}")

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
        image_id = built_image_id(daytona, built, image_name)
        if image_id is None:
            info(f"no image {image_name} to delete")
        else:
            delete_image(daytona, image_id)
            info(f"deleted image {image_id}")


if __name__ == "__main__":
    sys.exit(main())
