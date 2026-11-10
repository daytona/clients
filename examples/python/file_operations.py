#!/usr/bin/env python3
"""file-operations — upload, list, search, replace, and download files.

Uses the single-file helpers ``fs.write_bytes(remote_path, bytes)`` and
``fs.read_bytes(path)`` (bytes in / bytes out).

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/file_operations.py
"""

from __future__ import annotations

import json
import sys

from _common import RUN, client, failed, info, passed, require_started, sandbox_image, section


def main() -> int:
    daytona = client()
    sandbox = daytona.create(sandbox_image(daytona), name=f"files-{RUN}", wait_seconds=200)
    info(f"created sandbox id={sandbox.id}")

    ok = True
    try:
        if not require_started(sandbox):
            return 1
        d = "/tmp/project-files"

        section("mkdir + list")
        sandbox.fs.mkdir(d, mode="755")
        before = sandbox.fs.list("/tmp").items
        info("entries in /tmp: " + ", ".join(f.name for f in before))

        section("write_bytes (from in-memory bytes)")
        config = json.dumps({"name": "demo", "version": "1.0.0", "settings": {"debug": True}}, indent=2)
        sandbox.fs.write_bytes(f"{d}/config.json", config.encode())
        sandbox.fs.write_bytes(f"{d}/script.sh", b'#!/bin/bash\necho "Hello from script!"\n')
        info("uploaded config.json + script.sh")

        section("run: verify + run the uploaded script")
        info(sandbox.process.run(f"ls -la {d}").output.strip())
        sandbox.process.run(f"chmod +x {d}/script.sh")
        script_out = sandbox.process.run(f"./{d}/script.sh")
        info(f"script output: {script_out.output.strip()}")
        if script_out.exit_code == 0:
            passed("the uploaded script ran")
        else:
            failed(f"the uploaded script failed (exit={script_out.exit_code})")
            ok = False

        section("glob (by name pattern)")
        found = sandbox.fs.glob(d, "*.json")
        info(f"json files: {found.items}")

        section("replace (edit config in place)")
        sandbox.fs.replace([f"{d}/config.json"], '"debug": true', '"debug": false')

        section("read_bytes (read bytes back)")
        text = sandbox.fs.read_bytes(f"{d}/config.json").decode()
        info(f"config.json now:\n{text}")
        if '"debug": false' in text:
            passed("replace round-tripped through read_bytes")
        else:
            failed(f"expected the edited value in the downloaded file (got: {text})")
            ok = False
    finally:
        section("cleanup")
        daytona.delete(sandbox)
        info(f"deleted sandbox {sandbox.id}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
