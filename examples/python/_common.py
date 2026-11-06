"""Shared helpers for the Daytona Python examples.

Every example builds its ``Daytona`` client from the same environment variables
so they can all use the same Daytona deployment:

    DAYTONA_API_URL      Daytona API base          (default https://api.daytona.com)
    DAYTONA_API_KEY      bearer api key (dtn_...)   (required)
    DAYTONA_IMAGE        registry image sandboxes start from (default python:3.12-slim)
"""

from __future__ import annotations

import os
import re
import sys
import uuid

from daytona import AsyncDaytona, AsyncDaytonaV2Client, Daytona, DaytonaConfig, DaytonaV2Client

IMAGE = os.environ.get("DAYTONA_IMAGE", "python:3.12-slim")
# The project image IMAGE is registered as, shared by every example run.
IMAGE_NAME = "examples-" + re.sub(r"[^a-z0-9]+", "-", IMAGE.lower()).strip("-")
RUN = uuid.uuid4().hex[:8]

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


def sandbox_image(daytona: DaytonaV2Client) -> str:
    """The registered image to create sandboxes from.

    A sandbox is created from a project image, not a registry reference:
    reuse IMAGE's registration when the project has one, else register it
    (``images.pull``, ``POST /images``) and wait until it is ready.
    """
    for image in daytona.images.iter(name=IMAGE_NAME):
        if image.name != IMAGE_NAME:
            continue
        if image.status == "ready":
            return IMAGE_NAME
        if image.status not in ("failed", "error"):
            daytona.images.get(image.id, wait="ready")
            return IMAGE_NAME
        daytona.images.delete(image.id)  # A failed registration: register again.
    info(f"registering {IMAGE} as image {IMAGE_NAME}")
    daytona.images.pull(IMAGE, name=IMAGE_NAME)
    return IMAGE_NAME


def client() -> DaytonaV2Client:
    """Build the synchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return Daytona(_config()).v2


def async_client() -> AsyncDaytonaV2Client:
    """Build the asynchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return AsyncDaytona(_config()).v2
