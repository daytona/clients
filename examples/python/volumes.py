#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mount one volume into multiple sandboxes, including a volume subpath.

Run from examples with DAYTONA_API_KEY set:
    PYTHONPATH=python python python/volumes.py

Writes become available to other sandboxes asynchronously. See
``../volumes_share.py`` for a persistence round-trip with synchronization waits.
"""

from __future__ import annotations

import sys

from _common import IMAGE, RUN, client, info, require_started, section
from daytona import DaytonaError
from daytona.v2 import ImageBuilder, Sandbox, VolumeMount


def main() -> int:
    daytona = client()

    section("volumes.get(name, create=True)")
    volume = daytona.volumes.get(f"my-volume-{RUN}", create=True)
    info(f"volume id={volume.id} name={volume.name} status={volume.status}")

    boxes: list[Sandbox] = []
    try:
        section("sandbox 1: mount volume at /mnt/vol and write a file")
        mount1 = "/mnt/vol"
        s1 = daytona.create_from_build(ImageBuilder.from_registry(IMAGE), name=f"vol1-{RUN}", volumes=[VolumeMount(volume_id=volume.id, mount_path=mount1)], build_timeout=240, wait_seconds=240)
        boxes.append(s1)
        if not require_started(s1):
            return 1
        s1.fs.mkdir(f"{mount1}/new-dir", mode="755")
        s1.fs.write_bytes(f"{mount1}/new-file.txt", b"Hello, World!")
        info(f"s1 wrote {mount1}/new-file.txt")

        section("sandbox 2: mount the same volume at a different path")
        mount2 = "/mnt/other"
        s2 = daytona.create_from_build(ImageBuilder.from_registry(IMAGE), name=f"vol2-{RUN}", volumes=[VolumeMount(volume_id=volume.id, mount_path=mount2)], build_timeout=240, wait_seconds=240)
        boxes.append(s2)
        if not require_started(s2):
            return 1
        files = s2.fs.list(mount2).items
        names = ", ".join(f.name for f in files) or "(not yet synchronized)"
        info(f"s2 sees at {mount2}: {names}")

        section("sandbox 3: mount a subpath of the volume")
        mount3 = "/mnt/alice"
        s3 = daytona.create_from_build(ImageBuilder.from_registry(IMAGE), name=f"vol3-{RUN}", volumes=[VolumeMount(volume_id=volume.id, mount_path=mount3, subpath="users/alice")], build_timeout=240, wait_seconds=240)
        boxes.append(s3)
        if not require_started(s3):
            return 1
        s3.fs.write_bytes(f"{mount3}/alice-file.txt", b"Hello from Alice's subpath!")
        info(f"s3 wrote {mount3}/alice-file.txt (volume-root/users/alice/alice-file.txt)")
    finally:
        section("cleanup")
        for box in boxes:
            try:
                daytona.delete(box)
                info(f"deleted sandbox {box.id}")
            except DaytonaError as error:
                info(f"delete sandbox failed: {error}")
        try:
            daytona.volumes.delete(volume.id)
            info(f"deleted volume {volume.id}")
        except DaytonaError as error:
            info(f"volume delete failed (delete its sandboxes first): {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
