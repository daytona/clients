#!/usr/bin/env python3
"""git — clone a repo, stage a change, commit, branch, and read status.

Shows clone / add / commit / branches / create_branch / checkout_branch /
delete_branch / status / push / pull.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/git_ops.py
"""

from __future__ import annotations

import sys

from _common import RUN, client, failed, info, passed, require_started, sandbox_image, section


REPO_URL = "https://github.com/octocat/Hello-World"


def main() -> int:
    daytona = client()
    sandbox = daytona.create(sandbox_image(daytona), name=f"git-{RUN}", wait_seconds=200)
    info(f"created sandbox id={sandbox.id}")

    ok = True
    try:
        if not require_started(sandbox):
            return 1
        git = sandbox.git
        proc = sandbox.process
        repo = "/tmp/demo-repo"

        # python:3.x-slim ships without git; install it on demand.
        section("ensure git is available")
        has = proc.run("command -v git || (apt-get update && apt-get install -y git)")
        if has.exit_code != 0:
            failed(f"could not ensure git: {has.output.strip()}")
            return 1
        info(proc.run("git --version").output.strip())

        section("clone")
        git.clone(REPO_URL, repo, branch="master")
        info(f"cloned {REPO_URL} -> {repo}")

        section("status (freshly cloned -> clean)")
        st0 = git.status(repo)
        info(f"current branch={st0.current_branch}")

        section("add + commit a new file")
        sandbox.fs.write_bytes(f"{repo}/daytona-note.txt", f"hello from {RUN}\n".encode())
        git.add(repo, ["daytona-note.txt"])
        commit = git.commit(repo, "add daytona-note", author="Ada Lovelace", email="ada@example.com")
        info(f"commit hash={commit.hash}")

        section("branches + create_branch + checkout + delete")
        git.create_branch(repo, "feature")
        git.checkout_branch(repo, "feature")
        info(f"branches: {git.list_branches(repo).items}")
        git.checkout_branch(repo, "master")
        git.delete_branch(repo, "feature")
        info('created, checked out, then deleted branch "feature"')

        section("verify the commit landed")
        log = proc.run(f"git -C {repo} log --oneline -1")
        info(f"HEAD: {log.output.strip()}")
        if "add daytona-note" in log.output:
            passed("commit is visible in the log")
        else:
            failed(f"expected our commit at HEAD (got: {log.output.strip()})")
            ok = False
    finally:
        section("cleanup")
        daytona.delete(sandbox)
        info(f"deleted sandbox {sandbox.id}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
