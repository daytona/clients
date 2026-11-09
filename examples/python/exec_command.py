#!/usr/bin/env python3
"""exec-command — run shell commands and interpreter code.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/exec_command.py
"""

from __future__ import annotations

import sys

from _common import RUN, client, failed, info, sandbox_image, section

from daytona.v2 import Sandbox


def basic_exec(sandbox: Sandbox) -> bool:
    """Run a command and interpreter code; return whether both succeeded."""
    section("basic run + interpreter")
    ok = True

    cmd = sandbox.process.run('echo "Hello World from CMD!"')
    if cmd.exit_code != 0:
        failed(f"command failed (exit={cmd.exit_code})")
        ok = False
    else:
        info(f"cmd: {cmd.output.strip()}")

    code = sandbox.interpreter.run('print("Hello World from code!")', language="python")
    if code.process.exit_code != 0 or code.error is not None:
        failed(f"code failed (exit={code.process.exit_code}, error={code.error})")
        ok = False
    else:
        info(f"code: {code.logs.stdout.strip()}")

    return ok


def main() -> int:
    daytona = client()

    sandbox = daytona.create(sandbox_image(daytona), name=f"exec-{RUN}", wait_seconds=200)
    info(f"created sandbox id={sandbox.id} state={sandbox.state}")

    try:
        ok = basic_exec(sandbox)
    finally:
        section("cleanup")
        daytona.delete(sandbox)
        info(f"deleted sandbox {sandbox.id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
