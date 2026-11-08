# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

import sys
from collections.abc import Generator
from pathlib import Path

import pluggy
import pytest

# The generator is imported by tests for the rules it shares with them
# (_LOOP_FREE, _is_internal_binder, the type transform). Make it importable
# before collection so the test modules use ordinary top-level imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gen"))


# The SDK's own tree (an sdist unpacks just this) and the repository around it.
_SDK_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SDK_ROOT.parent
# Whether the tests run in a repository checkout, with the contracts, the
# TypeScript SDK, scripts/ and upgrade/ next to the SDK. An sdist has none of them.
IN_REPO_CHECKOUT = (_REPO_ROOT / "contract").is_dir() and (_REPO_ROOT / "scripts").is_dir()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, pluggy.Result[None], None]:
    """Skip, outside a repository checkout, a test that reads a repository file.

    Drift and parity tests read files next to the SDK (``contract/``,
    ``sdk-typescript/``, ``scripts/``, ``upgrade/``). From an sdist those are
    absent: such a test is skipped rather than failed. In a checkout a missing
    file still fails the test.
    """
    outcome = yield
    error = outcome.exception
    if IN_REPO_CHECKOUT or not isinstance(error, FileNotFoundError) or error.filename is None:
        return
    missing = Path(str(error.filename)).resolve()
    if missing.is_relative_to(_REPO_ROOT) and not missing.is_relative_to(_SDK_ROOT):
        outcome.force_exception(pytest.skip.Exception(f"repository file not in this tree: {missing}"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _long_poll_only_outside_the_events_suite(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every wait outside ``test_events.py`` exercises the long-poll path.

    Those tests drive ``RecordingTransport`` fixtures and count the polling
    loop's requests and back-off pauses; with the events hub on, the same wait
    would first try (and fail) to open a socket to the fixture's fake host.
    The hub has its own suite.
    """
    if "test_events" not in request.module.__name__:
        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "off")


@pytest.fixture(autouse=True)
def _v2_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The v2 client takes its API URL from DAYTONA_V2_API_URL; point every
    test at the offline host (tests that need another one setenv it) and drop
    any URL the developer's shell exports for v0 or for the 0.x SDK.
    DAYTONA_API_URL is set to a host no test may reach: 2.x never reads it."""
    monkeypatch.setenv("DAYTONA_V2_API_URL", "http://api.invalid")
    monkeypatch.delenv("DAYTONA_V0_API_URL", raising=False)
    monkeypatch.setenv("DAYTONA_API_URL", "http://never-read-by-2x.invalid")
