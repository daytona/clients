#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Live e2e for the Daytona Python SDK (daytona) against the Daytona API.

Drives SDK image pull/build, sandbox create, a toolbox process round-trip,
get/list/delete, and error contracts, through the root client (`Daytona().v2`).

Self-contained: every sandbox and image is uniquely named and torn down on exit.

Env:
    DAYTONA_API_URL API root (default https://api.daytona.com;
                    DAYTONA_V2_API_URL wins when set, as in the SDK)
    DAYTONA_API_KEY bearer api key (required)
    SMOKE_IMAGE      registry image for image pull/build (default python:3.12-slim)
    DAYTONA_SKIP_TOOLBOX if set, skip the toolbox process round-trip
    DAYTONA_SKIP_DOCKERFILE if set, skip the Dockerfile build + boot (reported as
                    SKIP, never PASS; for deployments without image builds)
    DAYTONA_KEEP if set, don't tear down created resources
"""

from __future__ import annotations

import os
import sys
import time
import traceback
import uuid

from daytona import Daytona as RootDaytona
from daytona import DaytonaConfig, DaytonaV2Client
from daytona.v2 import DaytonaError, ImageBuilder, UnsupportedOperationError

IMAGE = os.environ.get("SMOKE_IMAGE", "python:3.12-slim")
RUN = uuid.uuid4().hex[:8]
SNAP = f"daytona-py-{RUN}"
DFSNAP = f"daytona-py-df-{RUN}"
# Dockerfile that layers a marker file on top of the base image so a sandbox
# booted from the built snapshot can prove the RUN layer took effect.
DOCKERFILE = f"FROM {IMAGE}\nRUN echo daytona-built-{RUN} > /etc/daytona-marker\n"
SKIP_TOOLBOX = bool(os.environ.get("DAYTONA_SKIP_TOOLBOX"))
SKIP_DOCKERFILE = bool(os.environ.get("DAYTONA_SKIP_DOCKERFILE"))
KEEP = bool(os.environ.get("DAYTONA_KEEP"))

PASS = 0
FAIL = 0
SKIPPED = 0
_images: list = []
_boxes: list = []

C_BOLD, C_GREEN, C_RED, C_RESET = "\033[1m", "\033[32m", "\033[31m", "\033[0m"


def log(m: str) -> None:
    print(f"\n{C_BOLD}== {m}{C_RESET}")


def check(label: str, cond: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  {C_GREEN}PASS{C_RESET} {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  {C_RED}FAIL{C_RESET} {label}" + (f"  ({detail})" if detail else ""))
    return cond


def wait_ready(daytona: DaytonaV2Client, image_id: str, deadline_s: float = 900) -> str:
    """`images.get(wait="ready")` long-polls at most 60 s per call: repeat it
    until the image settles or the overall deadline passes."""
    deadline = time.monotonic() + deadline_s
    while True:
        status = daytona.images.get(image_id, wait="ready", timeout=60).status
        if status in ("ready", "failed") or time.monotonic() >= deadline:
            return status
        time.sleep(1)


def skip(label: str, reason: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  SKIP {label}  ({reason})")


def expect_error(label: str, fn, exc_type, status=None) -> None:
    try:
        fn()
        check(label, False, "expected error, got success")
    except exc_type as e:
        got = getattr(e, "status_code", None)
        check(label, status is None or got == status, f"status={got}")
    except Exception as e:
        check(label, False, f"wrong error type {type(e).__name__}: {str(e)[:80]}")


def main() -> int:
    # The v2 client reads DAYTONA_V2_API_URL, else DAYTONA_API_URL. Resolve one
    # URL with that precedence (default: https://api.daytona.com) and pin both, so an
    # exported DAYTONA_V2_API_URL can never send the SDK somewhere else.
    api_url = (
        (os.environ.get("DAYTONA_V2_API_URL") or "").strip()
        or (os.environ.get("DAYTONA_API_URL") or "").strip()
        or "https://api.daytona.com"
    )
    os.environ["DAYTONA_V2_API_URL"] = os.environ["DAYTONA_API_URL"] = api_url
    daytona = RootDaytona(
        DaytonaConfig(
            api_key=os.environ.get("DAYTONA_API_KEY", ""),
        )
    ).v2

    try:
        log("images: pull")
        t0 = time.time()
        image = daytona.images.pull(IMAGE, name=SNAP)
        _images.append(image)
        check(
            "image pull accepted",
            bool(image.id),
            f"{time.time() - t0:.1f}s status={image.status}",
        )
        check("image name echoed", image.name == SNAP)

        df_image = None
        if SKIP_DOCKERFILE:
            skip("dockerfile image build + boot", "DAYTONA_SKIP_DOCKERFILE")
        else:
            log("images: build from Dockerfile")
            t0 = time.time()
            df_image = daytona.images.build(
                ImageBuilder.from_dockerfile(DOCKERFILE), name=DFSNAP
            )
            _images.append(df_image)
            check(
                "dockerfile image accepted",
                bool(df_image.id),
                f"{time.time() - t0:.1f}s status={df_image.status}",
            )
            check("dockerfile image name echoed", df_image.name == DFSNAP)

        log("images: list + wait ready")
        check("list returns images", isinstance(daytona.images.list().items, list))
        for built in [image] + ([df_image] if df_image else []):
            status = wait_ready(daytona, built.id)
            check(f"image {built.name} ready", status == "ready", status)

        log("sandboxes: create from image -> started")
        t0 = time.time()
        box = daytona.create(SNAP)
        _boxes.append(box)
        check(
            "sandbox started",
            box.state == "started",
            f"{time.time() - t0:.1f}s id={box.id}",
        )
        check("sandbox has id", bool(box.id))

        log("sandboxes: get + list")
        check("get(id)", daytona.get(box.id).id == box.id)
        check("list includes ours", any(s.id == box.id for s in daytona.list().items))

        if not SKIP_TOOLBOX:
            log("toolbox: exec round-trip")
            r = box.process.run("echo daytona-$((6*7))")
            check(
                "process.run",
                r.exit_code == 0 and "daytona-42" in r.output,
                (r.output or "").strip(),
            )

            # Given two blocked roots sharing a tag and one independent root.
            tag = f"sdk-py-{RUN}"
            other_tag = f"sdk-py-other-{RUN}"
            first = box.process.start("read line", stdin="open", tag=tag)
            second = box.process.start("read line", stdin="open", tag=tag)
            unrelated = box.process.start("read line", stdin="open", tag=other_tag)
            tagged = box.process.list(tag=tag).items
            check(
                "process.list filters shared tag",
                {entry.id for entry in tagged} == {first.id, second.id}
                and all(entry.tag == tag for entry in tagged),
            )
            check(
                "process.list filters unrelated tag",
                {entry.id for entry in box.process.list(tag=other_tag).items}
                == {unrelated.id},
            )

            # A tagged command that leaves a detached child behind and exits.
            box.process.run(
                f"setsid sh -c 'echo $$ > /tmp/dtn-tag-{RUN}; exec sleep 300' >/dev/null 2>&1 & sleep 0.5",
                tag=tag,
            )
            detached = box.process.run(f"cat /tmp/dtn-tag-{RUN}").output.strip()

            # When the shared tag is killed, both roots exit but the other can finish.
            # A sandbox without process containment refuses the tag-wide kill.
            try:
                killed: list[str] | None = box.process.kill_all(tag=tag, timeout=10)
            except UnsupportedOperationError:
                killed = None
                skip("process.kill_all tag-kill checks", "no process containment on this sandbox")
                for root in (first, second, unrelated):
                    root.kill()
                # Without containment kill() does not reach the detached child either.
                if detached:
                    box.process.run(f"kill -9 {detached} 2>/dev/null; true")
            if killed is not None:
                check("process.kill_all returns root ids", {first.id, second.id} <= set(killed))
                alive = box.process.run(f"kill -0 {detached} 2>/dev/null && echo alive || echo gone")
                check("detached descendant killed", alive.output.strip() == "gone", detached)
                for root in (first, second):
                    stopped = root.wait(timeout=5)
                    check(
                        "tagged root killed",
                        stopped.reason == "signaled" and stopped.signal == "KILL",
                        root.id,
                    )
                unrelated.write_stdin("finish\n")  # read returns; the root exits 0
                survivor = unrelated.wait(timeout=5)
                check(
                    "unrelated root completes normally",
                    survivor.reason == "exited" and survivor.exit_code == 0,
                )

                # A fresh command with the killed tag is new work, not fenced out.
                reused = box.process.run("echo tag-reused", tag=tag, timeout=5)
                check("process tag reusable", reused.exit_code == 0 and "tag-reused" in reused.output)

        if df_image is not None:
            log("sandboxes: boot the Dockerfile-built image -> RUN layer applied")
            t0 = time.time()
            df_box = daytona.create(DFSNAP)
            _boxes.append(df_box)
            check(
                "dockerfile sandbox started",
                df_box.state == "started",
                f"{time.time() - t0:.1f}s id={df_box.id}",
            )
            if not SKIP_TOOLBOX:
                rm = df_box.process.run("cat /etc/daytona-marker")
                check(
                    "dockerfile RUN layer present",
                    rm.exit_code == 0 and f"daytona-built-{RUN}" in (rm.output or ""),
                    (rm.output or "").strip(),
                )

        log("bad input: unknown image -> 404")
        expect_error(
            "create(unknown image) 404",
            lambda: daytona.create(f"no-such-{RUN}"),
            DaytonaError,
            404,
        )
    except Exception:
        check("unexpected exception", False)
        traceback.print_exc()
    finally:
        teardown(daytona)

    log("results")
    print(
        f"  {C_GREEN}{PASS} passed{C_RESET}, {C_RED}{FAIL} failed{C_RESET}, {SKIPPED} skipped"
    )
    return 0 if FAIL == 0 else 1


def teardown(daytona: DaytonaV2Client) -> None:
    global FAIL
    if KEEP:
        log("teardown: skipped (DAYTONA_KEEP)")
        return
    log("teardown")
    for b in _boxes:
        try:
            daytona.delete(b)
        except Exception as e:
            FAIL += 1
            print(f"    box delete failed: {str(e)[:80]}")
    # Sandbox delete is asynchronous: the image stays referenced (409) until the
    # sandboxes are gone, so retry for a bounded time.
    for image in _images:
        for attempt in range(60):
            try:
                daytona.images.delete(image.id)
                break
            except DaytonaError as e:
                if getattr(e, "status_code", None) != 409 or attempt == 59:
                    FAIL += 1
                    print(f"    image delete failed: {str(e)[:80]}")
                    break
                time.sleep(2)


if __name__ == "__main__":
    if not os.environ.get("DAYTONA_API_KEY"):
        print(
            "DAYTONA_API_KEY is required (create a project API key in the Daytona dashboard)",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(main())
