#!/usr/bin/env python3
"""exec-command — run shell commands and interpreter code.

Run (from examples):
    DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \\
      python python/exec_command.py
"""

from __future__ import annotations

import sys

from _common import IMAGE, RUN, client, info, section

from daytona.v2 import Image


def basic_exec(sandbox) -> None:
    section("basic run + interpreter")

    cmd = sandbox.process.run('echo "Hello World from CMD!"')
    if cmd.exit_code != 0:
        print("command failed:", cmd.exit_code)
    else:
        info(f"cmd: {cmd.output.strip()}")

    code = sandbox.interpreter.run('print("Hello World from code!")', language="python")
    if code.process.exit_code != 0:
        print("code failed:", code.process.exit_code)
    else:
        info(f"code: {code.process.output.strip()}")


def main() -> int:
    daytona = client()

    sandbox = daytona.create(Image.base(IMAGE), name=f"exec-{RUN}", timeout=200)
    info(f"created sandbox id={sandbox.id} state={sandbox.state}")

    try:
        basic_exec(sandbox)
    finally:
        section("cleanup")
        daytona.delete(sandbox)
        info(f"deleted sandbox {sandbox.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
