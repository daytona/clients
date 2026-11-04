#!/usr/bin/env python3
"""build_logs — read the output of a Dockerfile build.

Builds a deliberately chatty Dockerfile as a sandbox and prints the build log
as text, including each `RUN` step's stdout and build errors.

``sandbox.build_logs()`` returns build state, available output and any
truncation notice. Use ``follow_build_logs()`` for live events and completion
metadata. The example also reads logs for a second sandbox created from the
same Dockerfile, then deliberately fails a build and prints its error output.

Run (from examples):
DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \\
      python python/build_logs.py
"""

from __future__ import annotations

import sys

from _common import IMAGE, RUN, client, failed, info, passed, section

from daytona.v2 import Image

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
    sandboxes = []

    try:
        section("build a Dockerfile as a sandbox")
        sandbox = daytona.create(
            Image.from_dockerfile(DOCKERFILE),
            name=f"buildlogs-{RUN}",
            timeout=400,
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
        twin = daytona.create(
            Image.from_dockerfile(DOCKERFILE),
            name=f"buildlogs-twin-{RUN}",
            timeout=400,
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
            broken = daytona.create(
                Image.from_dockerfile(BROKEN_DOCKERFILE),
                name=f"buildlogs-bad-{RUN}",
                # Bound the wait so a failed build does not stall the example.
                timeout=120,
            )
            sandboxes.append(broken)
            # Reaching here means the create did not fail; still print whatever
            # the build recorded rather than asserting a specific outcome.
            info(f"unexpectedly created {broken.id} ({broken.state})")
            show(broken.build_logs())
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
            else:
                # Find the failed sandbox by name to read the build's error output.
                target_name = f"buildlogs-bad-{RUN}"
                found = [s for s in daytona.iter() if s.name == target_name]
                if not found:
                    failed(
                        "the failed sandbox was not listed, so its build log "
                        "could not be shown"
                    )
                    ok = False
                else:
                    sandboxes.extend(found)
                    # Reading build logs can fail; report the error to the user.
                    try:
                        failed_log = found[0].build_logs()
                    except Exception as read_exc:
                        failed(f"the failed build's log could not be read: {read_exc}")
                        ok = False
                    else:
                        show(failed_log)
                        # A state line alone does not prove output was
                        # retrieved. Require our failing command's output.
                        if f"intentional-build-failure-{RUN}" in failed_log:
                            passed("the failed build's log contains the failing command's output")
                        else:
                            failed(
                                "the failed build's log is missing the failing command's output"
                            )
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
