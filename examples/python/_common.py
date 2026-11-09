"""Shared helpers for the Daytona Python examples.

Every example builds its ``Daytona`` client from the same environment variables
so they can all use the same Daytona deployment:

    DAYTONA_API_URL      Daytona API base          (default https://api.daytona.com)
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
from daytona.v2 import Image, NameConflictError

IMAGE = os.environ.get("DAYTONA_IMAGE", "python:3.12-slim")
# The project image IMAGE is registered as, shared by every example run. The
# digest of the full reference keeps distinct references apart (the readable
# part folds ':', '/' and '.' together).
IMAGE_NAME = (
    "examples-"
    + re.sub(r"[^a-z0-9]+", "-", IMAGE.lower()).strip("-")[:40]
    + "-"
    + hashlib.sha256(IMAGE.encode()).hexdigest()[:8]
)
RUN = uuid.uuid4().hex[:8]
# Server waits for a registering image (each returns after at most about 30 s).
_IMAGE_WAITS = 40

_BOLD, _GREEN, _RED, _RESET = "\033[1m", "\033[32m", "\033[31m", "\033[0m"


def section(title: str) -> None:
    print(f"\n{_BOLD}== {title}{_RESET}", flush=True)


def info(msg: str) -> None:
    print(f"   .. {msg}", flush=True)


def passed(msg: str) -> None:
    print(f"   {_GREEN}PASS{_RESET} {msg}", flush=True)


def failed(msg: str) -> None:
    print(f"   {_RED}FAIL{_RESET} {msg}", flush=True)


def _config() -> DaytonaConfig:
    api_key = os.environ.get("DAYTONA_API_KEY")
    if not api_key:
        print(
            "DAYTONA_API_KEY is required (mint a project API key from the dashboard)",
            file=sys.stderr,
        )
        sys.exit(2)
    # The v2 client reads DAYTONA_V2_API_URL, else DAYTONA_API_URL; default to
    # the Daytona API when neither is set.
    if (
        not (os.environ.get("DAYTONA_V2_API_URL") or "").strip()
        and not (os.environ.get("DAYTONA_API_URL") or "").strip()
    ):
        os.environ["DAYTONA_API_URL"] = "https://api.daytona.com"
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
    for _ in range(3):
        image = _registered(daytona)
        if image is None:
            info(f"registering {IMAGE} as image {IMAGE_NAME}")
            try:
                image = daytona.images.pull(IMAGE, name=IMAGE_NAME)
            except NameConflictError:
                continue  # Another run registered it first: use that one.
        # The server's wait returns after a while even when the image is not done.
        for _ in range(_IMAGE_WAITS):
            if image.status in ("ready", "failed", "error"):
                break
            image = daytona.images.get(image.id, wait="ready")
        if image.status == "ready":
            return IMAGE_NAME
        if image.status not in ("failed", "error"):
            raise RuntimeError(f"image {IMAGE_NAME} is still {image.status}")
        daytona.images.delete(image.id)  # A failed registration: register again.
    raise RuntimeError(f"could not register {IMAGE} as image {IMAGE_NAME}")


def client() -> DaytonaV2Client:
    """Build the synchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return Daytona(_config()).v2


def async_client() -> AsyncDaytonaV2Client:
    """Build the asynchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return AsyncDaytona(_config()).v2
