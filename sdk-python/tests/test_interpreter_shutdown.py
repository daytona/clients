# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Requests made while the interpreter shuts down (an ``atexit`` handler, a
thread still running after the program ended or while Python stops its
thread pools) get a typed ``ClientClosedError`` instead of a bare
``RuntimeError``, from the sync and the async client, and are refused before
anything is sent when they run in an ``atexit`` handler. A thread that
outlives the program keeps working while the pools it needs still run."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import types
from pathlib import Path
from typing import Dict, List

import pytest

from daytona.v2._shutdown import (
    SHUTDOWN_REFUSAL,
    interpreter_shutting_down,
    refuse_during_interpreter_shutdown,
    shutdown_refusal_for,
)
from daytona.v2.errors import ClientClosedError

ROOT = Path(__file__).resolve().parents[1]

# A local API answering every request 404 and closing the connection, so a
# later request needs a new connection; it records what it received.
_SERVER = """
import asyncio, atexit, json, os, sys, threading, time
sys.path.insert(0, {src!r})
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HITS = []

class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        HITS.append(self.command + " " + self.path)
        body = json.dumps({{"message": "no such sandbox", "code": "NOT_FOUND", "source": "DAYTONA_API"}}).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_DELETE = do_POST = _reply

    def log_message(self, *args):
        pass

SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
os.environ["DAYTONA_V2_API_URL"] = "http://{host}:%d" % SERVER.server_address[1]

def report(label, error=None):
    cause = None if error is None or error.__cause__ is None else type(error.__cause__).__name__
    print("RESULT " + json.dumps({{
        "label": label,
        "error": None if error is None else type(error).__name__,
        "code": getattr(error, "code", None),
        "message": None if error is None else str(error),
        "cause": cause,
        "hits": list(HITS),
    }}), flush=True)
"""


def _run(body: str, *, host: str = "localhost") -> List[Dict[str, object]]:
    script = _SERVER.format(src=str(ROOT / "src"), host=host) + textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
    reports = [
        json.loads(line[len("RESULT ") :])
        for line in result.stdout.splitlines()
        if line.startswith("RESULT ")
    ]
    assert reports, result.stdout[-2000:] + result.stderr[-2000:]
    return reports


def _assert_refused(report: Dict[str, object], hits: List[str]) -> None:
    assert report["error"] == "ClientClosedError"
    assert report["code"] == "CLIENT_CLOSED"
    assert report["message"] == SHUTDOWN_REFUSAL
    assert report["hits"] == hits, "the refused request must not reach the API"


def test_sync_delete_registered_with_atexit_is_refused_before_it_is_sent() -> None:
    (report,) = _run(
        """
        from daytona import Daytona, DaytonaConfig
        client = Daytona(DaytonaConfig(api_key="k"))
        try:
            client.v2.get("sbx-1")
        except Exception:
            pass

        def cleanup():
            try:
                client.v2.delete("sbx-1")
            except Exception as error:
                report("atexit", error)
            else:
                report("atexit")

        atexit.register(cleanup)
        """
    )
    _assert_refused(report, ["GET /sandboxes/sbx-1"])
    assert report["cause"] is None


def test_a_client_created_in_an_atexit_handler_is_refused_too() -> None:
    (report,) = _run(
        """
        from daytona import Daytona, DaytonaConfig

        def cleanup():
            try:
                Daytona(DaytonaConfig(api_key="k")).v2.delete("sbx-1")
            except Exception as error:
                report("atexit", error)
            else:
                report("atexit")

        atexit.register(cleanup)
        """
    )
    _assert_refused(report, [])


def test_async_client_run_from_an_atexit_handler_is_refused() -> None:
    (report,) = _run(
        """
        from daytona import AsyncDaytona, DaytonaConfig

        async def delete():
            async with AsyncDaytona(DaytonaConfig(api_key="k")) as client:
                await client.v2.delete("sbx-1")

        def cleanup():
            try:
                asyncio.run(delete())
            except Exception as error:
                report("atexit", error)
            else:
                report("atexit")

        atexit.register(cleanup)
        """
    )
    _assert_refused(report, [])


_WORKER = """
from daytona import Daytona, DaytonaConfig
client = Daytona(DaytonaConfig(api_key="k"))
try:
    client.v2.get("sbx-1")
except Exception:
    pass

def worker():
    deadline = time.monotonic() + 30
    while threading.main_thread().is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        client.v2.get("sbx-2")
    except Exception as error:
        report("worker", error)
    else:
        report("worker")

threading.Thread(target=worker).start()
"""


def test_a_thread_running_after_the_program_ended_still_sends_requests() -> None:
    # An IP literal needs no DNS lookup, so nothing the request needs has stopped.
    (report,) = _run(_WORKER, host="127.0.0.1")
    assert report["error"] == "NotFoundError"
    assert report["hits"] == ["GET /sandboxes/sbx-1", "GET /sandboxes/sbx-2"]


def test_a_request_that_needs_a_stopped_pool_after_the_program_ended_is_typed() -> None:
    # A host name needs a DNS lookup on a thread pool that has stopped by then.
    (report,) = _run(_WORKER, host="localhost")
    assert report["error"] == "ClientClosedError"
    assert report["code"] == "CLIENT_CLOSED"
    assert report["message"] == SHUTDOWN_REFUSAL
    assert report["cause"] == "RuntimeError"
    assert report["hits"] == ["GET /sandboxes/sbx-1"]


def _assert_mapped(report: Dict[str, object], hits: List[str]) -> None:
    assert report["error"] == "ClientClosedError"
    assert report["code"] == "CLIENT_CLOSED"
    assert report["message"] == SHUTDOWN_REFUSAL
    assert report["cause"] == "RuntimeError"
    assert report["hits"] == hits


def test_a_first_request_after_the_program_ended_is_typed() -> None:
    # The process's first DNS lookup creates its first thread pool, which
    # Python refuses to register once shutdown began.
    (report,) = _run(
        """
        from daytona import Daytona, DaytonaConfig
        client = Daytona(DaytonaConfig(api_key="k"))

        def worker():
            deadline = time.monotonic() + 30
            while threading.main_thread().is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            try:
                client.v2.get("sbx-2")
            except Exception as error:
                report("worker", error)
            else:
                report("worker")

        threading.Thread(target=worker).start()
        """
    )
    _assert_mapped(report, [])


def test_the_async_client_after_the_program_ended_is_typed() -> None:
    (report,) = _run(
        """
        from daytona import AsyncDaytona, DaytonaConfig

        async def get():
            async with AsyncDaytona(DaytonaConfig(api_key="k")) as client:
                await client.v2.get("sbx-2")

        def worker():
            deadline = time.monotonic() + 30
            while threading.main_thread().is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            try:
                asyncio.run(get())
            except Exception as error:
                report("worker", error)
            else:
                report("worker")

        threading.Thread(target=worker).start()
        """
    )
    _assert_mapped(report, [])


def test_a_request_while_python_stops_its_thread_pools_is_typed() -> None:
    # Python joins the thread pools' workers before it marks the main thread
    # finished: an executor job still running at exit holds that window open.
    (report,) = _run(
        """
        import concurrent.futures
        import concurrent.futures.thread as pools
        from daytona import Daytona, DaytonaConfig
        client = Daytona(DaytonaConfig(api_key="k"))
        try:
            client.v2.get("sbx-1")
        except Exception:
            pass
        concurrent.futures.ThreadPoolExecutor().submit(time.sleep, 3)

        def worker():
            deadline = time.monotonic() + 30
            while not pools._shutdown and time.monotonic() < deadline:
                time.sleep(0.005)
            alive = threading.main_thread().is_alive()
            try:
                client.v2.get("sbx-2")
            except Exception as error:
                report("main_alive=%s" % alive, error)
            else:
                report("main_alive=%s" % alive)

        threading.Thread(target=worker).start()
        """
    )
    assert report["label"] == "main_alive=True"
    _assert_mapped(report, ["GET /sandboxes/sbx-1"])


@pytest.mark.parametrize(
    "message",
    [
        "cannot schedule new futures after interpreter shutdown",
        "cannot schedule new futures after shutdown",
        "can't create new thread at interpreter shutdown",
        "can't register atexit after shutdown",
    ],
)
@pytest.mark.parametrize("flag", ["threading", "concurrent.futures"])
def test_a_stopped_pool_error_maps_once_python_began_stopping_its_pools(
    flag: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert shutdown_refusal_for(RuntimeError(message)) is None
    if flag == "threading":
        monkeypatch.setattr(threading, "_SHUTTING_DOWN", True, raising=False)
    else:
        monkeypatch.setitem(sys.modules, "concurrent.futures.thread", types.SimpleNamespace(_shutdown=True))
    assert isinstance(shutdown_refusal_for(RuntimeError(message)), ClientClosedError)
    assert shutdown_refusal_for(RuntimeError("some other failure")) is None
    assert interpreter_shutting_down() is False, "the program's own requests are not refused"


def test_requests_are_allowed_while_the_program_runs() -> None:
    assert interpreter_shutting_down() is False
    refuse_during_interpreter_shutdown()
    assert shutdown_refusal_for(RuntimeError("cannot schedule new futures after shutdown")) is None


def test_a_finalizing_interpreter_refuses_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "is_finalizing", lambda: True)
    assert interpreter_shutting_down() is True
    with pytest.raises(ClientClosedError) as raised:
        refuse_during_interpreter_shutdown()
    assert (raised.value.code, raised.value.source, raised.value.status_code) == (
        "CLIENT_CLOSED",
        "SDK",
        None,
    )
    assert isinstance(
        shutdown_refusal_for(RuntimeError("cannot schedule new futures after interpreter shutdown")),
        ClientClosedError,
    )
    assert shutdown_refusal_for(RuntimeError("some other failure")) is None
