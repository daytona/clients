"""Shared helpers for the Daytona Python examples.

Every example builds its ``Daytona`` client from the same environment variables
so they can all use the same Daytona deployment:

    DAYTONA_V2_API_URL   Daytona API base          (default https://api.daytona.com;
                         regional endpoint or test environment)
    DAYTONA_API_KEY      bearer api key (dtn_...)   (required)
    DAYTONA_IMAGE        registry image sandboxes start from (default python:3.12-slim)
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import uuid
from typing import Optional

from daytona import AsyncDaytona, AsyncDaytonaV2Client, Daytona, DaytonaConfig, DaytonaV2Client
from daytona.v2 import Image, NameConflictError, RequestTimeoutError, Sandbox

IMAGE = os.environ.get("DAYTONA_IMAGE", "python:3.12-slim")
# The project image IMAGE is registered as, shared by every example run. The
# digest of the full reference keeps distinct references apart (the readable
# part folds ':', '/' and '.' together).
def image_name(ref: str) -> str:
    """The project image name a registry reference is registered as."""
    readable = re.sub(r"[^a-z0-9]+", "-", ref.lower()).strip("-")[:40]
    return f"examples-{readable}-{hashlib.sha256(ref.encode()).hexdigest()[:8]}"


IMAGE_NAME = image_name(IMAGE)
RUN = uuid.uuid4().hex[:8]
# How long a registering image may take to settle, as one budgeted wait.
IMAGE_WAIT_SECONDS = 1200

_BOLD, _GREEN, _RED, _RESET = "\033[1m", "\033[32m", "\033[31m", "\033[0m"


def section(title: str) -> None:
    print(f"\n{_BOLD}== {title}{_RESET}", flush=True)


def info(msg: str) -> None:
    print(f"   .. {msg}", flush=True)


def passed(msg: str) -> None:
    print(f"   {_GREEN}PASS{_RESET} {msg}", flush=True)


def failed(msg: str) -> None:
    print(f"   {_RED}FAIL{_RESET} {msg}", flush=True)


def require_started(sandbox: Sandbox) -> bool:
    """Whether ``sandbox`` came up; a create returns its handle however its wait ended."""
    if sandbox.state == "started":
        return True
    reason = f": {sandbox.error}" if sandbox.error else ""
    raw = f" (raw state {sandbox.raw_state!r})" if sandbox.state == "unknown" else ""
    failed(f"sandbox {sandbox.id} is {sandbox.state}{raw}{reason}, not started")
    return False


def _config() -> DaytonaConfig:
    api_key = os.environ.get("DAYTONA_API_KEY")
    if not api_key:
        print(
            "DAYTONA_API_KEY is required (mint a project API key from the dashboard)",
            file=sys.stderr,
        )
        sys.exit(2)
    # The v2 client reads DAYTONA_V2_API_URL itself and defaults to the
    # Daytona API when it is unset.
    return DaytonaConfig(api_key=api_key)


def _registered(daytona: DaytonaV2Client) -> Optional[Image]:
    return next((i for i in daytona.images.iter(name=IMAGE_NAME) if i.name == IMAGE_NAME), None)


def sandbox_image(daytona: DaytonaV2Client) -> str:
    """The registered image to create sandboxes from.

    A sandbox is created from a project image, not a registry reference:
    reuse IMAGE's registration when the project has one (waiting while it
    registers), else register it (``images.pull``, ``POST /images``). A failed
    registration is deleted and registered again.
    """
    cause = "no registration attempt finished"
    for _ in range(3):
        image = _registered(daytona)
        if image is None:
            info(f"registering {IMAGE} as image {IMAGE_NAME}")
            try:
                image = daytona.images.pull(IMAGE, name=IMAGE_NAME, wait=False)
            except NameConflictError:
                continue  # Another run registered it first: use that one.
        if image.status not in ("ready", "failed", "error"):
            # One wait over the whole budget: it repeats the server's long poll
            # and returns once the image settles, or raises RequestTimeoutError
            # when the budget runs out.
            try:
                image = daytona.images.get(image.id, wait="ready", timeout=IMAGE_WAIT_SECONDS)
            except RequestTimeoutError:
                raise RuntimeError(f"image {IMAGE_NAME} did not settle within {IMAGE_WAIT_SECONDS} s") from None
        if image.status == "ready":
            return IMAGE_NAME
        # A failed registration: report why, then register again.
        cause = image.error or f"registration {image.status}"
        info(f"image {IMAGE_NAME} failed: {cause}; registering again")
        daytona.images.delete(image.id)
    raise RuntimeError(f"could not register {IMAGE} as image {IMAGE_NAME}: {cause}")


def client() -> DaytonaV2Client:
    """Build the synchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return Daytona(_config()).v2


def async_client() -> AsyncDaytonaV2Client:
    """Build the asynchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return AsyncDaytona(_config()).v2
