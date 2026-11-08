#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Live e2e for the Daytona Python SDK (daytona) against the Daytona API.

Drives SDK image pull/build, sandbox create, a toolbox process round-trip,
get/list/delete, and error contracts, through the root client (`Daytona().v2`).

Self-contained: every sandbox and image is uniquely named and torn down on exit.

Env:
    DAYTONA_V2_API_URL API root (default https://api.daytona.com; the SDK
                    reads it, and never reads DAYTONA_API_URL)
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
from daytona.v2 import DaytonaError, ImageBuilder, RequestTimeoutError

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


def wait_ready(daytona: DaytonaV2Client, image_id: str, timeout_s: float = 900) -> str:
    """One `images.get(wait="ready")` with the whole budget: it repeats the
    server's long poll and returns the image once it settles (`ready`,
    `failed` or `error`), or raises `RequestTimeoutError` when the budget runs
    out, reported here as a failed check."""
    try:
        return daytona.images.get(image_id, wait="ready", timeout=timeout_s).status
    except RequestTimeoutError:
        return f"not settled within {timeout_s:g} s"


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
    # The v2 client reads DAYTONA_V2_API_URL itself (default: https://api.daytona.com).
    daytona = RootDaytona(
        DaytonaConfig(
            api_key=os.environ.get("DAYTONA_API_KEY"),
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
        check("list returns images", isinstance(daytona.images.list().items, tuple))
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

        if not SKIP_TOOLBOX and box.state == "started":
            log("toolbox: exec round-trip")
            r = box.process.run("echo daytona-$((6*7))")
            check(
                "process.run",
                r.exit_code == 0 and "daytona-42" in r.output,
                (r.output or "").strip(),
            )

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
            if not SKIP_TOOLBOX and df_box.state == "started":
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
    # Image references can still be releasing after sandbox deletion has
    # completed; retry conflicts for a bounded time.
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
