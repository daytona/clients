#!/usr/bin/env python3
"""build_logs — read the output of a Dockerfile build.

Builds a deliberately chatty Dockerfile as a sandbox and prints the build log
as text, including each `RUN` step's stdout and build errors.

``sandbox.build_logs()`` returns build state, available output and any
truncation notice. Use ``follow_build_logs()`` for live events and completion
metadata. The example also reads logs for a second sandbox created from the
same Dockerfile, then deliberately fails a build and prints its error output.

Run (from examples):
DAYTONA_API_KEY=dtn_... \\
      python python/build_logs.py
"""

from __future__ import annotations

import sys

from _common import IMAGE, RUN, client, failed, info, passed, section

from daytona.v2 import ImageBuilder, ImageBuildFailedError, Sandbox

# Distinct, labelled steps make the build output easy to follow.
DOCKERFILE = f"""FROM {IMAGE}
RUN echo "STEP 1: who am i" && id
RUN echo "STEP 2: the marker" && echo build-logs-{RUN} > /marker && cat /marker
RUN echo "STEP 3: done"
"""

# `false` always exits non-zero, so this build cannot succeed. A unique marker
# lets us check that the failed command's output is available.
BROKEN_DOCKERFILE = f"""FROM {IMAGE}
RUN printf '%s%s\\n' 'intentional-build-failure-' '{RUN}' && false
"""


def show(logs: str) -> None:
    """Print a build log with a visible frame, so it stands out from the run."""
    print("   +" + "-" * 68)
    for line in logs.splitlines():
        print(f"   | {line}")
    print("   +" + "-" * 68)


def main() -> int:
    daytona = client()
    ok = True
    sandboxes: list[Sandbox] = []

    try:
        section("build a Dockerfile as a sandbox")
        sandbox = daytona.create_from_build(
            ImageBuilder.from_dockerfile(DOCKERFILE),
            name=f"buildlogs-{RUN}",
            build_timeout=400,
            wait_seconds=400,
        )
        sandboxes.append(sandbox)
        info(f"sandbox {sandbox.id} is {sandbox.state}")

        section("the build output")
        logs = sandbox.build_logs()
        show(logs)

        # Require the RUN step's output, not merely a build status line.
        if "STEP 2: the marker" in logs:
            passed("the log contains our RUN step's output")
        else:
            failed("the log did not contain the build's own output")
            ok = False
        if f"build-logs-{RUN}" in logs:
            passed("it shows the value the step actually produced")
        else:
            info("the marker value was not present in the available output")

        section("build logs for a second sandbox from the same Dockerfile")
        twin = daytona.create_from_build(
            ImageBuilder.from_dockerfile(DOCKERFILE),
            name=f"buildlogs-twin-{RUN}",
            build_timeout=400,
            wait_seconds=400,
        )
        sandboxes.append(twin)
        twin_logs = twin.build_logs()
        show(twin_logs)
        if "STEP 2: the marker" in twin_logs:
            passed("the second sandbox's build log contains the RUN step's output")
        else:
            failed("the second sandbox's build log is missing the RUN step's output")
            ok = False

        section("a FAILED build keeps its log, which says why")
        info("building something that cannot succeed — this takes ~20s")
        try:
            broken = daytona.create_from_build(
                ImageBuilder.from_dockerfile(BROKEN_DOCKERFILE),
                name=f"buildlogs-bad-{RUN}",
                # Bound the build separately from the sandbox readiness wait.
                build_timeout=120,
                wait_seconds=120,
            )
            sandboxes.append(broken)
            failed(f"the deliberately broken build unexpectedly created {broken.id} ({broken.state})")
            ok = False
        except Exception as exc:
            message = str(exc)
            info(f"create failed as expected: {message}")
            # A CLIENT timeout is not a build failure: it means the build
            # outlived the timeout, so this section proved nothing.
            # Distinguish them, or a slow build turns a silent non-test into
            # a PASS.
            if "timeout" in message.lower():
                failed(
                    "the create timed out rather than failing — the build "
                    "outlived the timeout, so this section proved nothing"
                )
                ok = False
            elif not isinstance(exc, ImageBuildFailedError) or exc.image_id is None:
                failed(f"expected ImageBuildFailedError carrying the image id, got: {message}")
                ok = False
            else:
                # A failed build leaves no sandbox; the error's image id reads
                # the failed build's output.
                chunks: list[str] = []
                read_failed = False
                try:
                    daytona.images.follow_build_logs(
                        exc.image_id, on_logs=chunks.append, timeout=60
                    )
                except Exception as read_exc:
                    # Reading build logs can fail; report the error to the user.
                    failed(f"the failed build's log could not be read: {read_exc}")
                    ok = False
                    read_failed = True
                failed_log = "".join(chunks)
                if failed_log:
                    show(failed_log)
                # After a read error the output may be partial, so judge only
                # a complete read. A state line alone does not prove output
                # was retrieved: require our failing command's output.
                if not read_failed:
                    if not failed_log:
                        failed("the failed build's log was empty")
                        ok = False
                    elif f"intentional-build-failure-{RUN}" in failed_log:
                        passed("the failed build's log contains the failing command's output")
                    else:
                        failed("the failed build's log is missing the failing command's output")
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
