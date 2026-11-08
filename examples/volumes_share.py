#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Share a volume across sandboxes and verify data survives sandbox deletion.

Sandbox A writes, B reads and appends, then C reads both writers' data.
Writes synchronize asynchronously; EXAMPLE_SYNC_WAIT_SECONDS sets the wait in seconds
before deleting a writer (default 20). Run from the repository root:

    DAYTONA_API_KEY=dtn_... python examples/volumes_share.py

DAYTONA_V2_API_URL (a regional endpoint or test environment) defaults to https://api.daytona.com. DAYTONA_IMAGE defaults to
python:3.12-slim. Set DAYTONA_KEEP=1 to retain the resources for inspection.
"""

from __future__ import annotations

import os
import sys
import time
import uuid

from daytona import Daytona, DaytonaConfig, DaytonaError
from daytona.v2 import ImageBuilder, Sandbox, VolumeMount

IMAGE = os.environ.get("DAYTONA_IMAGE", "python:3.12-slim")
SYNC_WAIT_SECONDS = int(os.environ.get("EXAMPLE_SYNC_WAIT_SECONDS", "20"))
KEEP = bool(os.environ.get("DAYTONA_KEEP"))
RUN = uuid.uuid4().hex[:8]


def sh(sandbox: Sandbox, command: str) -> str:
    """Run a shell command and return trimmed stdout; raise if it exits non-zero."""
    result = sandbox.process.run(command)
    output = (result.output or "").strip()
    if result.exit_code != 0:
        raise RuntimeError(f"{command!r} failed (exit={result.exit_code}): {output}")
    return output


def main() -> int:
    daytona = Daytona(DaytonaConfig(api_key=os.environ.get("DAYTONA_API_KEY"))).v2
    volume = daytona.volumes.create(name=f"shared-vol-{RUN}")
    print(f"volume id={volume.id} name={volume.name} status={volume.status}")
    boxes: list[Sandbox] = []
    ok = True

    def new_box(name: str) -> Sandbox:
        box = daytona.create_from_build(ImageBuilder.from_registry(IMAGE), name=f"{name}-{RUN}", volumes=[VolumeMount(volume_id=volume.id, mount_path="/data")], build_timeout=240, wait_seconds=240)
        boxes.append(box)
        # A create returns its handle however its wait ended: check before using it.
        if box.state != "started":
            raise RuntimeError(f"sandbox {box.id} is {box.state}, not started")
        return box

    try:
        print("1. sandbox A writes /data/message.txt")
        a = new_box("vol-a")
        marker = f"hello-from-A-{RUN}"
        sh(a, f"echo {marker} > /data/message.txt && sync")
        print(f"waiting {SYNC_WAIT_SECONDS}s for A's changes to synchronize")
        time.sleep(SYNC_WAIT_SECONDS)
        a.delete()
        boxes.remove(a)

        print("2. sandbox B reads A's file and appends a line")
        b = new_box("vol-b")
        got = sh(b, "if [ -e /data/message.txt ]; then cat /data/message.txt; else echo '<missing>'; fi")
        if got == marker:
            print("PASS: B read A's file after A was deleted")
        else:
            print(f"FAIL: B did not see A's data: {got!r}")
            ok = False
        marker_b = f"hello-from-B-{RUN}"
        sh(b, f"echo {marker_b} >> /data/message.txt && sync")
        print(f"waiting {SYNC_WAIT_SECONDS}s for B's changes to synchronize")
        time.sleep(SYNC_WAIT_SECONDS)
        b.delete()
        boxes.remove(b)

        print("3. sandbox C reads both writers' data")
        c = new_box("vol-c")
        contents = sh(c, "if [ -e /data/message.txt ]; then cat /data/message.txt; fi")
        if marker in contents and marker_b in contents:
            print("PASS: C sees changes from both A and B")
        else:
            print(f"FAIL: C is missing a writer's data: {contents!r}")
            ok = False
    finally:
        if not KEEP:
            for box in boxes:
                try:
                    box.delete()
                except DaytonaError as error:
                    print(f"delete sandbox failed: {error}")
            # delete() waits until each sandbox is destroyed.
            try:
                daytona.volumes.delete(volume.id, id_only=True)
            except DaytonaError as error:
                print(f"delete volume failed (retrying once): {error}")
                time.sleep(5)
                daytona.volumes.delete(volume.id, id_only=True)
    print("SUCCESS" if ok else "FAILURE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
