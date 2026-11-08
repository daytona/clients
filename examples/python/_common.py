"""Shared helpers for the Daytona Python examples.

Every example builds its ``Daytona`` client from the same environment variables
so they can all use the same Daytona deployment:

    DAYTONA_V2_API_URL   Daytona API base          (default https://api.daytona.com;
                         regional endpoint or test environment)
    DAYTONA_API_KEY      bearer api key (dtn_...)   (required)
    DAYTONA_IMAGE        default sandbox image     (default python:3.12-slim)
"""

from __future__ import annotations

import os
import sys
import uuid

from daytona import AsyncDaytona, AsyncDaytonaV2Client, Daytona, DaytonaConfig, DaytonaV2Client
from daytona.v2 import Sandbox

IMAGE = os.environ.get("DAYTONA_IMAGE", "python:3.12-slim")
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


def client() -> DaytonaV2Client:
    """Build the synchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return Daytona(_config()).v2


def async_client() -> AsyncDaytonaV2Client:
    """Build the asynchronous v2 client (the root's ``.v2``) from the shared env vars, or exit with a hint."""
    return AsyncDaytona(_config()).v2
