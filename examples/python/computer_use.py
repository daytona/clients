#!/usr/bin/env python3
"""computer-use — start a sandbox's desktop, drive it, and record it (opt-in).

Needs a project image with a desktop, in a region where the platform offers
desktop automation; elsewhere the computer-use calls raise
``AddonUnavailableError`` (503, ``ADDON_UNAVAILABLE``).

Run (from examples):
    DAYTONA_API_KEY=dtn_... DAYTONA_DESKTOP_IMAGE=<image name or img_ id> \\
      python python/computer_use.py
"""

from __future__ import annotations

import os
import sys
import time

from _common import RUN, client, failed, info, require_started, section

from daytona.v2 import AddonUnavailableError, Sandbox

# Recording statuses whose file is usable. Any other one (`failed`,
# `interrupted`, or a status this release does not know) is not.
USABLE_RECORDING = ("completed", "duration_limit_reached", "size_limit_reached")
DESKTOP_WAIT_SECONDS = float(os.environ.get("EXAMPLE_DESKTOP_WAIT_SECONDS", "120"))
POLL_SECONDS = float(os.environ.get("EXAMPLE_POLL_SECONDS", "2"))


def wait_for_desktop(sandbox: Sandbox) -> bool:
    """Poll the desktop until it is ready; return whether it got there."""
    deadline = time.monotonic() + DESKTOP_WAIT_SECONDS
    while True:
        status = sandbox.computer_use.status()
        if status.desktop == "ready":
            return True
        if status.desktop == "failed" or time.monotonic() >= deadline:
            cause = f": {status.cause.message}" if status.cause is not None else ""
            failed(f"desktop is {status.desktop}{cause}")
            return False
        time.sleep(POLL_SECONDS)


def drive_and_record(sandbox: Sandbox) -> bool:
    """Record a few desktop actions; return whether the recording is usable."""
    section("desktop")
    sandbox.computer_use.start()
    if not wait_for_desktop(sandbox):
        return False
    info("desktop ready")

    recording = sandbox.computer_use.recording.start(label=f"example-{RUN}")
    info(f"recording {recording.id} started")
    try:
        sandbox.computer_use.mouse.move(x=200, y=200)
        sandbox.computer_use.keyboard.type(text="hello from daytona")
        shot = sandbox.computer_use.screen.screenshot()
        info(f"screenshot: {shot.size_bytes} bytes")
    finally:
        recording = sandbox.computer_use.recording.stop(id=recording.id)

    section("recording")
    # A stop that returns is not a usable file: check the status first.
    if recording.status not in USABLE_RECORDING:
        failed(f"recording {recording.id} ended {recording.status}; its file is not usable")
        return False
    data = sandbox.computer_use.recording.download(recording.id)
    info(f"recording {recording.file_name}: {len(data)} bytes, status {recording.status}")
    return True


def main() -> int:
    image = os.environ.get("DAYTONA_DESKTOP_IMAGE")
    if not image:
        print("DAYTONA_DESKTOP_IMAGE is required (a project image with a desktop)", file=sys.stderr)
        return 2
    daytona = client()

    sandbox = daytona.create(image, name=f"computer-use-{RUN}", wait_seconds=200)
    info(f"created sandbox id={sandbox.id} state={sandbox.state}")

    try:
        ok = require_started(sandbox) and drive_and_record(sandbox)
    except AddonUnavailableError as error:
        failed(f"desktop automation is not available for this sandbox: {error}")
        ok = False
    finally:
        section("cleanup")
        daytona.delete(sandbox)
        info(f"deleted sandbox {sandbox.id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
