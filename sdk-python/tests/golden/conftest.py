# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams, Daytona

# Unit lanes collect this directory without credentials; ignoring the tests at
# collection keeps them green while the golden lane still runs everything.
collect_ignore_glob = ["test_*.py"] if not os.getenv("DAYTONA_API_KEY") else []

logger = logging.getLogger(__name__)

# The assertions pin image-specific behavior (zsh resolution, $HOME, python
# availability), so the suite must run on the same image everywhere.
# DAYTONA_GOLDEN_SNAPSHOT lets a local stack point at the production image.
GOLDEN_SNAPSHOT = os.getenv("DAYTONA_GOLDEN_SNAPSHOT")


def _golden_params(**kwargs: object) -> CreateSandboxFromSnapshotParams:
    if GOLDEN_SNAPSHOT:
        kwargs["snapshot"] = GOLDEN_SNAPSHOT
    return CreateSandboxFromSnapshotParams(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def daytona_client() -> Iterator[Daytona]:
    yield Daytona()


@pytest.fixture(scope="module")
def sandbox(daytona_client: Daytona):
    sb = daytona_client.create(_golden_params(), timeout=120)
    try:
        yield sb
    finally:
        try:
            daytona_client.delete(sb)
        except Exception:
            logger.warning("golden cleanup: failed to delete sandbox %s", sb.id, exc_info=True)


@pytest.fixture(scope="module")
def code_run_sandboxes(daytona_client: Daytona):
    sandboxes = {
        "python": daytona_client.create(_golden_params(language="python"), timeout=120),
        "javascript": daytona_client.create(_golden_params(language="javascript"), timeout=120),
        "typescript": daytona_client.create(_golden_params(language="typescript"), timeout=120),
    }
    try:
        yield sandboxes
    finally:
        for sb in sandboxes.values():
            try:
                daytona_client.delete(sb)
            except Exception:
                logger.warning("golden cleanup: failed to delete sandbox %s", sb.id, exc_info=True)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def async_daytona_client() -> AsyncIterator[AsyncDaytona]:
    async with AsyncDaytona() as daytona:
        yield daytona


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def async_sandbox(async_daytona_client: AsyncDaytona):
    sb = await async_daytona_client.create(_golden_params(), timeout=120)
    try:
        yield sb
    finally:
        try:
            await async_daytona_client.delete(sb)
        except Exception:
            logger.warning("golden cleanup: failed to delete sandbox %s", sb.id, exc_info=True)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def async_code_run_sandboxes(async_daytona_client: AsyncDaytona):
    sandboxes = {
        "python": await async_daytona_client.create(_golden_params(language="python"), timeout=120),
        "javascript": await async_daytona_client.create(_golden_params(language="javascript"), timeout=120),
        "typescript": await async_daytona_client.create(_golden_params(language="typescript"), timeout=120),
    }
    try:
        yield sandboxes
    finally:
        for sb in sandboxes.values():
            try:
                await async_daytona_client.delete(sb)
            except Exception:
                logger.warning("golden cleanup: failed to delete sandbox %s", sb.id, exc_info=True)
