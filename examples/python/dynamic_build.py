#!/usr/bin/env python3
"""dynamic_build — create sandboxes from Dockerfiles and read their build logs.

Pass an inline Dockerfile as the image source and Daytona builds it and boots
the result as the sandbox, in one call — no image to register first.

Create two sandboxes from the same Dockerfile, inspect each build log, then
change the Dockerfile and verify the new file content. Create durations are
reported for information only; no particular timing is required.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/dynamic_build.py
"""

from __future__ import annotations

import sys
import time

from _common import IMAGE, RUN, client, failed, info, passed, section

from daytona.v2 import ImageBuilder


def main() -> int:
    daytona = client()
    ok = True

    # A unique marker lets us verify the Dockerfile's output in each sandbox.
    dockerfile = f"FROM {IMAGE}\nRUN echo dynamic-build-{RUN} > /marker\n"
    sandboxes = []

    try:
        section("create a sandbox straight from a Dockerfile (builds it)")
        started = time.monotonic()
        first = daytona.create_from_build(
            ImageBuilder.from_dockerfile(dockerfile),
            name=f"dynbuild-first-{RUN}",
            # Covers the image build and the sandbox start.
            wait_seconds=400,
        )
        sandboxes.append(first)
        first_secs = time.monotonic() - started
        info(f"first create took {first_secs:.1f}s")

        marker = first.process.run("cat /marker")
        info(f"marker: {marker.output.strip()}")
        if marker.exit_code == 0 and RUN in marker.output:
            passed("the sandbox booted the image built from our Dockerfile")
        else:
            failed(f"the Dockerfile's marker file is missing (exit={marker.exit_code})")
            ok = False

        section("build logs")
        logs = first.build_logs()
        info(f"build log is {len(logs)} chars")
        # Text can contain only build state; a nonempty response alone does not
        # prove that the Dockerfile's output was retrieved.
        if logs.strip():
            info("build_logs() returned a one-shot text response (state and any retained output)")
        else:
            info("build log response was empty")

        section("create a second sandbox from the same Dockerfile")
        started = time.monotonic()
        second = daytona.create_from_build(
            ImageBuilder.from_dockerfile(dockerfile),
            name=f"dynbuild-second-{RUN}",
            wait_seconds=400,
        )
        sandboxes.append(second)
        second_secs = time.monotonic() - started
        info(f"second create took {second_secs:.1f}s")

        marker2 = second.process.run("cat /marker")
        if marker2.exit_code == 0 and RUN in marker2.output:
            passed("the second sandbox contains the expected marker")
        else:
            failed("the second sandbox does not contain the expected marker")
            ok = False

        # Timing is reported, never asserted.
        info(f"elapsed: first {first_secs:.1f}s vs second {second_secs:.1f}s")

        section("read the second sandbox's build logs")
        second_logs = second.build_logs()
        info(second_logs or "build log response was empty")

        section("change the Dockerfile's output")
        other = daytona.create_from_build(
            ImageBuilder.from_dockerfile(f"FROM {IMAGE}\nRUN echo other-{RUN} > /marker\n"),
            name=f"dynbuild-other-{RUN}",
            wait_seconds=400,
        )
        sandboxes.append(other)
        other_marker = other.process.run("cat /marker").output.strip()
        info(f"marker: {other_marker}")
        if other_marker.startswith("other-"):
            passed("the changed Dockerfile produced the expected file content")
        else:
            failed("the changed Dockerfile's output is missing")
            ok = False
    finally:
        section("cleanup")
        for sandbox in sandboxes:
            try:
                daytona.delete(sandbox)
                info(f"deleted {sandbox.id}")
            except Exception as exc:
                info(f"could not delete {sandbox.id}: {exc}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
