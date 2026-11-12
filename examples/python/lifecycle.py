#!/usr/bin/env python3
"""lifecycle — create, get, list, run, and delete a sandbox.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/lifecycle.py
"""

from __future__ import annotations

import sys

from _common import RUN, client, failed, info, passed, require_started, sandbox_image, section


def main() -> int:
    daytona = client()

    section("create")
    sandbox = daytona.create(sandbox_image(daytona), name=f"life-{RUN}", wait_seconds=200)
    info(f"created id={sandbox.id} state={sandbox.state} cpu={sandbox.cpu} mem={sandbox.memory}")

    ok = True
    try:
        if not require_started(sandbox):
            return 1
        section("get(id) + list")
        got = daytona.get(sandbox.id)
        info(f"get(id) -> {got.id}")
        info(f"list finds it: {any(s.id == sandbox.id for s in daytona.iter())}")

        section("run round-trip")
        res = sandbox.process.run('echo "Hello World from run!"')
        info(f"exit={res.exit_code} out={res.output.strip()}")
        if res.exit_code == 0:
            passed("sandbox lifecycle round-trip completed")
        else:
            failed(f"run failed (exit={res.exit_code})")
            ok = False
    finally:
        section("delete")
        daytona.delete(sandbox)
        info(f"deleted {sandbox.id}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
