#!/usr/bin/env python3
"""dockerfile_sandbox — the happy path: build a Dockerfile, get a working
sandbox, do real work in it.

One call takes an inline Dockerfile to a RUNNING sandbox: Daytona builds the
image, boots it, and hands back something you can immediately run commands in.
No image to register first, no build to poll.

The Dockerfile bakes in a script, a data file and an environment variable, and
the sandbox then proves all three survived into the running sandbox — which is
how you know it booted YOUR image and not the stock base.

Deliberately uses only shell builtins, so it works whatever ``DAYTONA_IMAGE``
is set to (alpine and debian-slim differ on package managers, not on ``sh``).

Set ``DAYTONA_KEEP=1`` to leave the sandbox running at the end instead of
deleting it, so you can poke at it yourself.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/dockerfile_sandbox.py
"""

from __future__ import annotations

import os
import sys

from _common import IMAGE, RUN, client, failed, info, passed, require_started, section

from daytona.v2 import ImageBuilder

# Everything here is baked at BUILD time. If the sandbox can see it at RUN
# time, the image it booted is the one we just built.
#
# The ENV value has no spaces, so the example stays independent of how the
# image's environment is quoted.
DOCKERFILE = f"""FROM {IMAGE}
ENV GREETING=hello-from-the-image
RUN mkdir -p /app \\
 && printf '#!/bin/sh\\necho "$GREETING"\\necho "built-for: {RUN}"\\n' > /app/greet.sh \\
 && chmod +x /app/greet.sh
RUN echo '{{"build":"{RUN}","ok":true}}' > /app/build.json
WORKDIR /app
"""


def main() -> int:
    daytona = client()
    ok = True
    sandbox = None
    keep = os.environ.get("DAYTONA_KEEP") == "1"

    try:
        section("build the Dockerfile and boot it, in one call")
        sandbox = daytona.create_from_build(
            ImageBuilder.from_dockerfile(DOCKERFILE),
            name=f"dockerfile-sandbox-{RUN}",
            build_timeout=400,
            wait_seconds=400,
        )
        info(f"sandbox {sandbox.id}")
        info(f"state   {sandbox.state}")
        if not require_started(sandbox):
            return 1
        passed("the sandbox is running")

        section("run the script the Dockerfile baked in")
        greet = sandbox.process.run("/app/greet.sh")
        for line in greet.output.strip().splitlines():
            info(line)
        if greet.exit_code == 0 and RUN in greet.output:
            passed("the baked script ran, and it is OUR build")
        else:
            failed(f"the baked script did not run as expected (exit={greet.exit_code})")
            ok = False

        section("the ENV baked by the Dockerfile is set in the sandbox")
        env = sandbox.process.run("printenv GREETING")
        info(env.output.strip())
        if "hello-from-the-image" in env.output:
            passed("ENV survived into the running sandbox")
        else:
            failed("the Dockerfile's ENV is missing")
            ok = False

        section("WORKDIR and the baked data file")
        pwd = sandbox.process.run("pwd && cat build.json")
        for line in pwd.output.strip().splitlines():
            info(line)
        if "/app" in pwd.output and RUN in pwd.output:
            passed("WORKDIR and the baked file are both there")
        else:
            failed("WORKDIR or the baked file is missing")
            ok = False

        section("write and read a file at runtime")
        sandbox.fs.write_bytes("/app/runtime.txt", b"written by the example\n")
        back = sandbox.fs.read_bytes("/app/runtime.txt").decode()
        info(back.strip())
        if "written by the example" in back:
            passed("the filesystem round-trips")
        else:
            failed("the runtime file did not round-trip")
            ok = False

        section("create a second sandbox from the same Dockerfile")
        twin = daytona.create_from_build(
            ImageBuilder.from_dockerfile(DOCKERFILE),
            name=f"dockerfile-sandbox-twin-{RUN}",
            build_timeout=400,
            wait_seconds=400,
        )
        try:
            info(f"sandbox {twin.id} is {twin.state}")
            twin_greet = twin.process.run("/app/greet.sh") if require_started(twin) else None
            if twin_greet is not None and twin_greet.exit_code == 0 and RUN in twin_greet.output:
                passed("the second sandbox runs the expected script")
            else:
                failed("the second sandbox is not running the expected script")
                ok = False
        finally:
            # Guarded like the outer cleanup: a delete failure here must not
            # replace the assertion failure above with a teardown error.
            try:
                daytona.delete(twin)
                info(f"deleted {twin.id}")
            except Exception as exc:
                info(f"could not delete {twin.id}: {exc}")
    finally:
        section("cleanup")
        if sandbox is not None and not keep:
            try:
                daytona.delete(sandbox)
                info(f"deleted {sandbox.id}")
            except Exception as exc:
                info(f"could not delete {sandbox.id}: {exc}")
        elif sandbox is not None:
            info(f"DAYTONA_KEEP=1 — leaving {sandbox.id} ({sandbox.state}) in place")
            info(f'connect with: daytona.get("{sandbox.id}")')

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
