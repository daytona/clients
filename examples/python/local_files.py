#!/usr/bin/env python3
"""local_files — copy local files into an image with ImageBuilder.

The builder declares the image the way 0.x ``Image`` did: a base, a project
directory, a file and a working directory. Building it packs
the Dockerfile and those files into a build context, uploads it and boots a
sandbox from the result. The project directory's ``.dockerignore`` keeps a log
file out of the image.

The script writes its small project into a temporary directory, so it runs
from anywhere.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/local_files.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from _common import IMAGE, RUN, client, failed, info, passed, require_started, section

from daytona.v2 import ImageBuilder


def write_project(root: Path) -> None:
    (root / "app").mkdir()
    (root / "app" / "main.sh").write_text(f'#!/bin/sh\necho "local-files-{RUN}"\n')
    (root / "app" / "main.sh").chmod(0o755)
    (root / "app" / "debug.log").write_text("should not reach the image\n")
    (root / "app" / ".dockerignore").write_text("*.log\n")
    (root / "settings.conf").write_text(f"run={RUN}\n")


def main() -> int:
    daytona = client()
    ok = True
    sandbox = None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root)
        builder = (
            ImageBuilder.from_registry(IMAGE)
            .add_local_dir(root / "app", "/app")
            .add_local_file(root / "settings.conf", "/etc/example/")
            .workdir("/app")
        )
        info("Dockerfile:")
        for line in builder.to_dockerfile().splitlines():
            info(f"  {line}")
        try:
            section("build the image with its local files and boot it")
            sandbox = daytona.create_from_build(
                builder, name=f"local-files-{RUN}", build_timeout=400, wait_seconds=400
            )
            if not require_started(sandbox):
                return 1

            section("the copied files are in the sandbox")
            run = sandbox.process.run("./main.sh && cat /etc/example/settings.conf")
            for line in run.output.strip().splitlines():
                info(line)
            if run.exit_code == 0 and f"local-files-{RUN}" in run.output and f"run={RUN}" in run.output:
                passed("the directory and the file were copied")
            else:
                failed(f"the copied files are missing (exit={run.exit_code})")
                ok = False

            section("the .dockerignore kept the log out")
            ls = sandbox.process.run("ls -a /app")
            info(" ".join(ls.output.split()))
            if "debug.log" in ls.output or ".dockerignore" in ls.output:
                failed("an ignored file reached the image")
                ok = False
            else:
                passed("debug.log and .dockerignore are not in the image")
        finally:
            section("cleanup")
            if sandbox is not None:
                try:
                    daytona.delete(sandbox)
                    info(f"deleted {sandbox.id}")
                except Exception as exc:
                    info(f"could not delete {sandbox.id}: {exc}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
