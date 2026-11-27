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

import math
import os
import sys
import time

from _common import RUN, client, failed, info, require_started, section

from daytona.v2 import AddonUnavailableError, Sandbox

# Statuses of a final recording (stop() returns one) whose file is usable. Any
# other one is not: `failed` has no file, `interrupted` may be partial, and a
# status this release does not know is treated the same way.
USABLE_RECORDING = ("completed", "duration_limit_reached", "size_limit_reached")


def seconds(name: str, default: float) -> float:
    """A duration from the environment; exits on a value that is not a finite, non-negative number."""
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        print(f"{name} must be a non-negative number of seconds, got {raw!r}", file=sys.stderr)
        sys.exit(2)
    return value


def wait_for_desktop(sandbox: Sandbox, wait_seconds: float, poll_seconds: float) -> bool:
    """Poll the desktop until it is ready; return whether it got there."""
    deadline = time.monotonic() + wait_seconds
    while True:
        status = sandbox.computer_use.status()
        if status.desktop == "ready":
            return True
        if status.desktop == "failed" or time.monotonic() >= deadline:
            cause = f": {status.cause.message}" if status.cause is not None else ""
            failed(f"desktop is {status.desktop}{cause}")
            return False
        time.sleep(poll_seconds)


def drive_and_record(sandbox: Sandbox, wait_seconds: float, poll_seconds: float) -> bool:
    """Record a few desktop actions; return whether the recording is usable."""
    section("desktop")
    sandbox.computer_use.start()
    if not wait_for_desktop(sandbox, wait_seconds, poll_seconds):
        return False
    info("desktop ready")

    recording = sandbox.computer_use.recording.start(label=f"example-{RUN}")
    info(f"recording {recording.id} started")
    try:
        sandbox.computer_use.mouse.move(x=200, y=200)
        sandbox.computer_use.keyboard.type(text="hello from daytona")
        shot = sandbox.computer_use.screen.screenshot()
        info(f"screenshot: {shot.size_bytes} bytes")
    except BaseException:
        # Stop the recording, but keep the error that ended the actions.
        try:
            sandbox.computer_use.recording.stop(id=recording.id)
        except Exception as stop_error:
            failed(f"could not stop recording {recording.id}: {stop_error}")
        raise
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
    wait_seconds = seconds("EXAMPLE_DESKTOP_WAIT_SECONDS", 120)
    poll_seconds = seconds("EXAMPLE_POLL_SECONDS", 2)
    daytona = client()

    sandbox = daytona.create(image, name=f"computer-use-{RUN}", wait_seconds=200)
    info(f"created sandbox id={sandbox.id} state={sandbox.state}")

    try:
        ok = require_started(sandbox) and drive_and_record(sandbox, wait_seconds, poll_seconds)
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
