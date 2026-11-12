# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Process-wide WaitHub for sandbox state events.

One hub per process per (loop, api_url, api_key) identity, shared across
``Daytona`` instances via ref-counting; one WebSocket per service endpoint or
region with live waiters.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import random
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import (
    Awaitable,
    Callable,
    Dict,
    Final,
    FrozenSet,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Set,
    Tuple,
    Union,
)
from urllib.parse import quote, urlsplit
from weakref import WeakValueDictionary

from . import _clock
from ._auth import ProjectTokenAuth
from ._body_limit import DEFAULT_MAX_RESPONSE_BYTES
from ._core import RequestSpec, expect_object, loads_json, sandbox_state
from ._transport import AsyncTransport, _request_deadline
from ._wait import WAIT_FOR_STATE_MIN_INTERVAL_S
from .errors import (
    AuthenticationError,
    DaytonaError,
    ForbiddenError,
    NotFoundError,
    RequestTimeoutError,
    SandboxTerminalStateError,
    create_error,
)
from .types import SandboxEventsConfig, SandboxState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Bound at import: the hub's own pacing (backoff, the fallback window, the
# reconcile timer) is not the polling loop's, which tests patch through
# ``asyncio.sleep``.
_sleep = asyncio.sleep

_SUBPROTOCOL: Final = "daytona.events.v1"
_TICKET_MINT_SECONDS: Final = 30.0
_TICKET_PROTOCOL_PREFIX: Final = "daytona.ticket."
_BACKOFF_BASE_S: Final = 0.250
_BACKOFF_CAP_S: Final = 30.0
_MAX_IDS_PER_FRAME: Final = 1_000
_FALLBACK_RETRY_S: Final = 60.0
_TRANSIENT_FAILURE_THRESHOLD: Final = 3
#: Consecutive ``4401`` closes before the waiters are told the credential
#: cannot open a socket (see the socket loop).
_MAX_CREDENTIAL_REFUSALS: Final = 3
#: The largest server frame the hub accepts (1 MiB; parity with TypeScript).
_MAX_SERVER_FRAME_BYTES: Final = 1 << 20
#: How long to wait before re-subscribing an id a budget refused.
_REFUSED_RETRY_S: Final = 2.0
_RECONCILE_JITTER: Final = 0.10  # ±10 %
_FALLBACK_SLICE_MS: Final = 30_000
_FALLBACK_MIN_INTERVAL_S: Final = 0.2
_KNOWN_IDS_CAP: Final = 10_000
_EARLY_PROBE_S: Final = 10.0
_CLIENT_PING_AFTER_S: Final = 5.0
_NO_FRAME_TIMEOUT_S: Final = 70.0
_SUBSCRIBE_FLUSH_S: Final = 0.05
#: An id a waiter holds that has heard no state frame (and sent no subscribe)
#: for its stale threshold is re-subscribed at the next sweep tick; the server
#: answers with a fresh snapshot. This bounds the delay a missed event adds to
#: a wait, which neither the early probe (held off while the socket is live,
#: keepalives included) nor the reconcile timer does. The threshold is half the
#: wait's age, at least ``_STALE_AFTER_MIN_S`` and at most ``_STALE_AFTER_S``:
#: a young wait (a create just past the server's hold, most of which start
#: within a few seconds) is re-subscribed about 1, 2, 4 and 8 s in, then every
#: ~3 s. The ceiling rises to ``_STALE_AFTER_SLOW_S`` once the wait is older
#: than ``_STALE_SLOW_AFTER_S``: a long wait on a resting sandbox then costs a
#: snapshot every ~15 s, not every ~3 s.
_STALE_AFTER_MIN_S: Final = 1.0
_STALE_AFTER_S: Final = 3.0
_STALE_AFTER_SLOW_S: Final = 15.0
_STALE_SLOW_AFTER_S: Final = 60.0
_STALE_SWEEP_TICK_S: Final = 1.0
#: Ids the sweep re-subscribes per 1 s tick at most, about 1,500 per second per
#: socket (one client and region): under the server's per-socket subscribe
#: rate (2,000 ids/s) even with the tick's jitter, leaving room for fresh
#: subscribes and refused retries. Past it the oldest go first and the rest
#: wait a tick.
_STALE_SWEEP_IDS_PER_S: Final = 1_500

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_BARE_UUID_RE: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def region_of(sandbox_id: str) -> str:
    """Return the region tag from a sandbox id, ``"_"`` when it has none.

    A bare 8-4-4-4-12 UUID
    carries no tag; otherwise the tag is everything after the FIRST ``-``
    (base58 never contains one), unless the dash leads or trails the id.
    """
    if not sandbox_id or _BARE_UUID_RE.match(sandbox_id):
        return "_"
    dash = sandbox_id.find("-")
    if dash <= 0 or dash == len(sandbox_id) - 1:
        return "_"
    return sandbox_id[dash + 1 :]


def _ws_url(api_url: str, region: str) -> str:
    """The socket URL for a region, derived from the API URL.

    The API's base PATH is kept: an api served at
    ``https://host/platform/api`` answers the upgrade at
    ``/platform/api/events/{region}``, and dropping the prefix would dial the
    host root — where nothing answers — while the ticket was minted through
    the full path.
    """
    parsed = urlsplit(api_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    base = parsed.path.rstrip("/")
    return f"{ws_scheme}://{parsed.netloc}{base}/events/{region}"


def _ticket_url(api_url: str, region: str) -> str:
    return f"{api_url.rstrip('/')}/events/{region}/ticket"


def _normalize_hub_url_identity(raw_url: str) -> str:
    stripped = raw_url.rstrip("/")
    parsed = urlsplit(stripped)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    return re.sub(
        r"^([a-z][a-z0-9+.-]*://)([^/?#]+)",
        lambda match: f"{match.group(1).lower()}{match.group(2).lower()}",
        stripped,
        flags=re.IGNORECASE,
    )


def _jittered(value: float, fraction: float = _RECONCILE_JITTER) -> float:
    """Apply ± fraction jitter to value."""
    return value * (1.0 + random.uniform(-fraction, fraction))


def _backoff(attempt: int) -> float:
    """Full-jitter exponential backoff (seconds)."""
    window = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2**attempt))
    return random.uniform(0.0, window)


def _chunks(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def _events_disabled_env() -> bool:
    val = os.environ.get("DAYTONA_SANDBOX_EVENTS", "").strip().lower()
    return val in ("0", "false", "off")


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TargetPredicate:
    """Resolve when state ∈ states; reject when state ∈ terminal and ∉ states.

    ``create()`` waits for ``started`` and raises when the sandbox fails.
    """

    states: FrozenSet[SandboxState]
    terminal_states: FrozenSet[SandboxState]
    kind: Literal["target"] = "target"


_Predicate = _TargetPredicate
_EvalResult = Literal["resolve", "reject", "keep"]


def _evaluate(predicate: _Predicate, state: SandboxState, sandbox_id: str) -> _EvalResult:
    """Evaluate a predicate against the current decoded SDK state.

    ``unknown`` — a status this SDK does not recognize — is neither a match nor
    terminal: every wait keeps waiting through it.
    """
    if state == "unknown":
        return "keep"
    if state in predicate.states:
        return "resolve"
    if state in predicate.terminal_states:
        return "reject"
    return "keep"


def _predicate_fallback_wait_state(predicate: _Predicate) -> Optional[str]:
    """The wire ``wait=`` value for the long-poll fallback, when one target
    maps to one wire status; ``None`` means plain polling."""
    if len(predicate.states) != 1:
        return None
    for s, wire in (
        ("started", "running"),
        ("stopped", "stopped"),
        ("paused", "paused"),
        ("destroyed", "deleted"),
    ):
        if s in predicate.states:
            return wire
    return None


# The terminal states the exact ``wait_exact=deleted`` long poll still answers
# on: ``deleted`` itself and ``failed``/``lost`` (public ``error``).
_EXACT_DELETED_ANSWERS: FrozenSet[str] = frozenset({"destroyed", "error"})


def wait_query_key(wire_state: str, terminal_states: Iterable[str]) -> str:
    """The query parameter that long-polls for ``wire_state`` in a wait that
    rejects on ``terminal_states``.

    ``deleted`` uses the exact form: the loose ``wait=deleted`` is already met
    by ``deleting``, so a deletion that takes a while to finish would answer
    every poll at once instead of holding it. The exact form answers on
    ``failed`` and ``lost`` too, but on no other state, so a wait that also
    rejects on another state keeps the loose form and sees it at once."""
    if wire_state == "deleted" and set(terminal_states) <= _EXACT_DELETED_ANSWERS:
        return "wait_exact"
    return "wait"


# ---------------------------------------------------------------------------
# Waiter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WaitOutcome:
    """What a wait resolved with: the decoded state and the wire status."""

    state: SandboxState
    raw_state: str


@dataclass
class _Waiter:
    sandbox_id: str
    predicate: _Predicate
    future: asyncio.Future[WaitOutcome]
    observe: Optional[Callable[[WaitOutcome], None]] = None
    early_probe_task: Optional[asyncio.Task[None]] = None
    deadline_at: Optional[float] = None
    registered_at: float = 0.0


# ---------------------------------------------------------------------------
# Internal exceptions
# ---------------------------------------------------------------------------


class _AuthError(Exception):
    def __init__(self, error: Exception) -> None:
        self.error = error
        super().__init__(str(error))


class _PermanentFallbackError(Exception):
    pass


class _ReconnectError(Exception):
    def __init__(self, message: str, *, credential_refused: bool = False) -> None:
        super().__init__(message)
        self.credential_refused = credential_refused


class _HubFallback(Exception):
    """Raised from a waiter future when the hub enters fallback mode.

    The catching hub.wait() should delegate to sandbox_poll_fn (if provided)
    or to _fallback_wait() using the hub's own HTTP client.
    """


class _WsConn(Protocol):
    """Minimal interface required from a WebSocket connection object."""

    async def recv(self) -> str: ...

    async def send(self, data: str) -> None: ...

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Socket connection state
# ---------------------------------------------------------------------------


@dataclass
class _SocketState:
    key: str
    region: str
    subscriptions: Set[str] = field(default_factory=set)
    consecutive_failures: int = 0
    credential_refusals: int = 0
    reconnect_attempt: int = 0
    fallback_until: Optional[float] = None
    permanent_fallback: bool = False
    task: Optional[asyncio.Task[None]] = None
    reconcile_task: Optional[asyncio.Task[None]] = None
    stale_task: Optional[asyncio.Task[None]] = None
    ws: Optional[_WsConn] = None
    versions: Dict[str, int] = field(default_factory=dict)
    snapshot_pending: Dict[str, int] = field(default_factory=dict)
    # When each subscribed id was last sent a subscribe or given a state frame.
    heard_at: Dict[str, float] = field(default_factory=dict)
    # When the current wait on each subscribed id began (its stale threshold
    # grows with the wait's age).
    waited_since: Dict[str, float] = field(default_factory=dict)
    # The last decoded state per subscribed id: a new waiter on an id the
    # socket already holds is judged against it at once (a sandbox resting in
    # its target state emits no further event to wait for).
    states: Dict[str, SandboxState] = field(default_factory=dict)
    raw_states: Dict[str, str] = field(default_factory=dict)
    # Set when a waiter registers; the socket loop sleeps on it while it has
    # nothing to subscribe instead of polling.
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    # When the socket last had a live waiter (for ``idle_close``).
    idle_since: Optional[float] = None
    refused_retry_ids: Set[str] = field(default_factory=set)
    refused_retry_task: Optional[asyncio.Task[None]] = None
    pending_subscribe_ids: Set[str] = field(default_factory=set)
    pending_unsubscribe_ids: Set[str] = field(default_factory=set)
    # Ids whose pending unsubscribe a re-subscribe cancelled in this window:
    # the server still holds them, so if the re-subscribe is dropped too, the
    # unsubscribe must be sent after all.
    unsubscribe_replaced_ids: Set[str] = field(default_factory=set)
    # Serialises the sends of overlapping flushes, so a later flush's
    # unsubscribe can never overtake an earlier flush's subscribe of the id.
    flush_send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribe_flush_task: Optional[asyncio.Task[None]] = None
    last_frame_at: float = field(default_factory=_clock.monotonic)
    client_ping_sent_for_frame_at: Optional[float] = None


# ---------------------------------------------------------------------------
# Default connector
# ---------------------------------------------------------------------------


def endpoint_from_headers(headers: Mapping[str, str]) -> Optional[str]:
    """The service endpoint identified by a sandbox mutation response, if set."""
    return headers.get("x-daytona-cell") or None


async def _default_connector(uri: str, subprotocols: List[str]) -> _WsConn:
    from websockets.asyncio.client import connect

    class _NoRedirects(connect):
        # The SDK follows no redirect (as TypeScript's WebSocket clients): a
        # 3xx handshake is a failed connection, and the ticket in the
        # subprotocol header never goes to the `Location` host.
        def process_redirect(self, exc: Exception) -> Exception | str:
            return exc

    # `max_size`: the largest server frame accepted (the websockets default,
    # made explicit so it is a decision, not an accident; the TypeScript hub
    # enforces the same 1 MiB). A larger frame closes the socket 1009.
    return await _NoRedirects(
        uri, subprotocols=subprotocols, open_timeout=30, max_size=_MAX_SERVER_FRAME_BYTES
    )


# ---------------------------------------------------------------------------
# WaitHub
# ---------------------------------------------------------------------------


class WaitHub:
    """Process-wide hub: one WebSocket per endpoint, subscribe-by-id, hub-evaluated waits.

    Created once per (loop, api_url, api_key, …) identity via the registry;
    ref-counted by attached ``Daytona`` instances. Never instantiate directly —
    use :func:`attach_hub` / :func:`detach_hub`.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        source: str = "sdk-python-async",
        config: Optional[SandboxEventsConfig] = None,
        connector: Optional[Callable[[str, List[str]], Awaitable[_WsConn]]] = None,
        loop: asyncio.AbstractEventLoop | None = None,
        auth: Optional[ProjectTokenAuth] = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._source = source
        self._config: SandboxEventsConfig = config if config is not None else SandboxEventsConfig()
        self._connector: Callable[[str, List[str]], Awaitable[_WsConn]] = connector or _default_connector

        # key → socket
        self._sockets: Dict[str, _SocketState] = {}
        # Region → service endpoint learned from response headers.
        self._learned: Dict[str, str] = {}
        # sandbox_id → waiters
        self._waiters: Dict[str, List[_Waiter]] = {}
        # sandbox_ids for which ANY socket has received a real snapshot/event
        self._known: OrderedDict[str, None] = OrderedDict()

        self._ref_count: int = 0
        self._closed: bool = False
        self._loop: asyncio.AbstractEventLoop | None = loop
        self._early_probe_s: float = _EARLY_PROBE_S
        self._client_ping_after_s: float = _CLIENT_PING_AFTER_S
        self._subscribe_flush_s: float = _SUBSCRIBE_FLUSH_S
        self._stale_after_min_s: float = _STALE_AFTER_MIN_S
        self._stale_after_s: float = _STALE_AFTER_S
        self._stale_after_slow_s: float = _STALE_AFTER_SLOW_S
        self._stale_slow_after_s: float = _STALE_SLOW_AFTER_S
        self._stale_sweep_tick_s: float = _STALE_SWEEP_TICK_S
        # Unset: derived from the tick (`_stale_sweep_cap`).
        self._stale_sweep_max_ids: Optional[int] = None

        # The hub's own API transport (the SDK's, not a bare HTTP
        # client): the ticket mint and the long-poll fallback carry the same
        # identity headers and typed errors as every other request. Its own
        # instance because the hub outlives any one ``Daytona``.
        # `auth`: a JWT-configured client's project-token exchange, so the
        # ticket mint is authenticated the same way (there is no API key then).
        # `max_response_bytes`: the attaching client's cap on buffered bodies.
        self._api = AsyncTransport(
            self._api_url, api_key or None, source=source, auth=auth, max_response_bytes=max_response_bytes
        )

    @property
    def enabled(self) -> bool:
        """Whether waits ride the socket at all (config, then the environment)."""
        return self._config.enabled and not _events_disabled_env()

    # ------------------------------------------------------------------
    # Ref counting
    # ------------------------------------------------------------------

    def _attach(self) -> None:
        self._ref_count += 1

    async def _detach(self) -> None:
        self._ref_count -= 1
        if self._ref_count <= 0:
            await self._close_hub()

    async def _close_hub(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in list(self._sockets.values()):
            if sock.task is not None and not sock.task.done():
                sock.task.cancel()
            if sock.reconcile_task is not None and not sock.reconcile_task.done():
                sock.reconcile_task.cancel()
            if sock.stale_task is not None and not sock.stale_task.done():
                sock.stale_task.cancel()
            if sock.refused_retry_task is not None and not sock.refused_retry_task.done():
                sock.refused_retry_task.cancel()
            if sock.subscribe_flush_task is not None and not sock.subscribe_flush_task.done():
                sock.subscribe_flush_task.cancel()
            sock.pending_subscribe_ids.clear()
            sock.pending_unsubscribe_ids.clear()
            sock.unsubscribe_replaced_ids.clear()
            sock.refused_retry_ids.clear()
        err = create_error("hub closed", 503, "SERVICE_UNAVAILABLE", "SDK")
        for waiters in self._waiters.values():
            for w in waiters:
                self._cancel_waiter_probe(w)
                if not w.future.done():
                    w.future.set_exception(err)
        self._waiters.clear()
        await self._api.close()

    def _discard_closed_loop_resources(self) -> None:
        self._closed = True
        for sock in self._sockets.values():
            for task in (sock.task, sock.reconcile_task, sock.stale_task, sock.refused_retry_task):
                if task is not None and not task.done():
                    task.cancel()
            sock.task = None
            sock.reconcile_task = None
            sock.stale_task = None
            sock.refused_retry_task = None
            if sock.subscribe_flush_task is not None and not sock.subscribe_flush_task.done():
                sock.subscribe_flush_task.cancel()
            sock.subscribe_flush_task = None
            sock.ws = None
            sock.subscriptions.clear()
            sock.heard_at.clear()
            sock.waited_since.clear()
            sock.states.clear()
            sock.raw_states.clear()
            sock.versions.clear()
            sock.snapshot_pending.clear()
            sock.pending_subscribe_ids.clear()
            sock.pending_unsubscribe_ids.clear()
            sock.unsubscribe_replaced_ids.clear()
            sock.refused_retry_ids.clear()
        self._sockets.clear()
        for waiters in self._waiters.values():
            for waiter in waiters:
                self._cancel_waiter_probe(waiter)
                if not waiter.future.done():
                    waiter.future.cancel()
        self._waiters.clear()
        self._loop = None

    def _cancel_waiter_probe(self, waiter: _Waiter) -> None:
        task = waiter.early_probe_task
        if task is not None and not task.done():
            task.cancel()
        waiter.early_probe_task = None

    def _waiter_still_pending(self, waiter: _Waiter) -> bool:
        return waiter in self._waiters.get(waiter.sandbox_id, []) and not waiter.future.done()

    def _initial_probe_delay_s(self, waiter: _Waiter) -> float:
        delay_s = self._early_probe_s
        if waiter.deadline_at is None:
            return delay_s
        remaining_s = waiter.deadline_at - _clock.monotonic()
        if remaining_s <= 0:
            return 0.0
        return min(delay_s, remaining_s / 2)

    # ------------------------------------------------------------------
    # Endpoint discovery (called by Sandbox / Daytona on mutation responses)
    # ------------------------------------------------------------------

    def learn(self, sandbox_id: str, endpoint: str) -> None:
        """Record a region's service endpoint; no-op if the endpoint is empty."""
        if not endpoint:
            return
        region = region_of(sandbox_id)
        if region == "_":
            return
        previous_endpoint = self._learned.get(region)
        if previous_endpoint == endpoint:
            return
        self._learned[region] = endpoint
        self._rekey_socket(region, previous_endpoint, endpoint)

    def _rekey_socket(self, region: str, previous_endpoint: Optional[str], endpoint: str) -> None:
        """Upgrade a region-keyed socket to an endpoint key after discovery."""
        region_sock = self._sockets.get(region)
        previous_endpoint_sock = (
            self._sockets.get(previous_endpoint) if previous_endpoint is not None else None
        )
        endpoint_sock = self._sockets.get(endpoint)

        if previous_endpoint_sock is not None and previous_endpoint != endpoint:
            destination = endpoint_sock or self._ensure_socket(endpoint, region)
            self._move_socket_subscriptions(
                previous_endpoint, previous_endpoint_sock, destination, preserve_state=False
            )
            return

        if region_sock is None:
            return  # will be endpoint-keyed on first need

        if endpoint_sock is None:
            # Simple rename: region socket becomes endpoint socket
            del self._sockets[region]
            region_sock.key = endpoint
            self._sockets[endpoint] = region_sock
        else:
            self._move_socket_subscriptions(region, region_sock, endpoint_sock, preserve_state=True)

    def _move_socket_subscriptions(
        self,
        source_key: str,
        source: _SocketState,
        destination: _SocketState,
        *,
        preserve_state: bool,
    ) -> None:
        moved_ids = list(source.subscriptions)
        for sandbox_id in moved_ids:
            destination.subscriptions.add(sandbox_id)
            source_version = source.versions.get(sandbox_id)
            destination_version = destination.versions.get(sandbox_id)
            if source_version is not None and (
                destination_version is None or source_version > destination_version
            ):
                destination.versions[sandbox_id] = source_version
            pending_version = source.snapshot_pending.get(sandbox_id)
            if pending_version is not None:
                destination.snapshot_pending[sandbox_id] = pending_version
            waited_since = source.waited_since.get(sandbox_id)
            if waited_since is not None:
                # The newer start of the two, as a new waiter would set it.
                destination.waited_since[sandbox_id] = max(
                    waited_since, destination.waited_since.get(sandbox_id, waited_since)
                )
            if preserve_state:
                source_state = source.states.get(sandbox_id)
                if source_state is not None:
                    destination.states[sandbox_id] = source_state
                source_raw_state = source.raw_states.get(sandbox_id)
                if source_raw_state is not None:
                    destination.raw_states[sandbox_id] = source_raw_state
            if sandbox_id in source.refused_retry_ids:
                destination.refused_retry_ids.add(sandbox_id)

        source.subscriptions.clear()
        source.heard_at.clear()
        source.waited_since.clear()
        source.states.clear()
        source.raw_states.clear()
        source.snapshot_pending.clear()
        self._clear_pending_subscribe_ids(source)
        source.refused_retry_ids.clear()
        if source.refused_retry_task is not None and not source.refused_retry_task.done():
            source.refused_retry_task.cancel()
        if source.task is not None and not source.task.done():
            source.task.cancel()
        if source.reconcile_task is not None and not source.reconcile_task.done():
            source.reconcile_task.cancel()
        if source.stale_task is not None and not source.stale_task.done():
            source.stale_task.cancel()
        del self._sockets[source_key]
        destination.wake.set()
        self._ensure_socket_task(destination)
        if destination.ws is not None and moved_ids:
            asyncio.get_running_loop().create_task(self._send_subscribe_ids(destination, moved_ids))
        if destination.ws is not None and self._has_any_waiter_for(destination):
            # A connected destination with no waiter until now runs no sweep.
            self._ensure_stale_sweep(destination)
        if destination.refused_retry_ids and (
            destination.refused_retry_task is None or destination.refused_retry_task.done()
        ):
            destination.refused_retry_task = asyncio.get_running_loop().create_task(
                self._resubscribe_refused_batch(destination)
            )

    # ------------------------------------------------------------------
    # Service endpoint selection
    # ------------------------------------------------------------------

    def _socket_key_for(self, sandbox_id: str, region_hint: Optional[str] = None) -> Tuple[str, str]:
        """Return (key, region) for this sandbox id.

        key is the service endpoint if learned, else the region tag.
        region is always the tag used in the WebSocket URL path.
        """
        region = region_of(sandbox_id)
        if region == "_" and region_hint:
            region = region_hint
        endpoint = self._learned.get(region)
        key = endpoint if endpoint else region
        return key, region

    def _ensure_socket(self, key: str, region: str) -> _SocketState:
        if key not in self._sockets:
            self._sockets[key] = _SocketState(key=key, region=region)
        return self._sockets[key]

    def _ensure_socket_task(self, sock: _SocketState) -> None:
        if sock.task is None or sock.task.done():
            sock.task = asyncio.get_running_loop().create_task(self._socket_loop(sock))

    def _remember_known(self, sandbox_id: str) -> None:
        self._known[sandbox_id] = None
        self._known.move_to_end(sandbox_id)
        while len(self._known) > _KNOWN_IDS_CAP:
            self._known.popitem(last=False)

    # ------------------------------------------------------------------
    # Public wait entry point
    # ------------------------------------------------------------------

    async def wait(
        self,
        sandbox_id: str,
        predicate: _Predicate,
        *,
        timeout: Optional[float] = None,
        region_hint: Optional[str] = None,
        sandbox_poll_fn: Optional[Callable[[], Awaitable[WaitOutcome]]] = None,
        on_observed: Optional[Callable[[WaitOutcome], None]] = None,
    ) -> WaitOutcome:
        """Wait for a predicate on sandbox_id.

        Returns the resolving :class:`WaitOutcome` (decoded state + wire status).
        ``sandbox_poll_fn`` — if provided — is used as the fallback poller
        instead of the hub's own HTTP client, so callers can supply the
        sandbox's own transport-aware ``refresh_data`` as the poll source.
        """
        deadline = None if (timeout is None or timeout == 0) else _clock.monotonic() + timeout

        def _remaining() -> Optional[float]:
            return None if deadline is None else deadline - _clock.monotonic()

        async def _use_fallback() -> WaitOutcome:
            rem = _remaining()
            if sandbox_poll_fn is not None:
                return await _run_poll_loop(sandbox_id, predicate, deadline=deadline, fn=sandbox_poll_fn)
            return await self._fallback_wait(sandbox_id, predicate, timeout=rem)

        # A closed hub runs no socket loop, so a waiter registered on it would
        # never be decided. A handle made before close() still works: it polls.
        if not self.enabled or self._closed:
            return await _use_fallback()

        key, region = self._socket_key_for(sandbox_id, region_hint)
        sock = self._ensure_socket(key, region)

        if sock.permanent_fallback or (
            sock.fallback_until is not None and _clock.monotonic() < sock.fallback_until
        ):
            return await _use_fallback()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[WaitOutcome] = loop.create_future()
        waiter = _Waiter(
            sandbox_id=sandbox_id,
            predicate=predicate,
            future=future,
            observe=on_observed,
            deadline_at=deadline,
        )

        if sandbox_id not in self._waiters:
            self._waiters[sandbox_id] = []
        self._waiters[sandbox_id].append(waiter)
        sock.idle_since = None

        held = (
            sock.states.get(sandbox_id)
            if sandbox_id in sock.subscriptions and sandbox_id not in sock.snapshot_pending
            else None
        )
        if held is not None:
            verdict = _evaluate(predicate, held, sandbox_id)
            if verdict == "resolve":
                self._cancel_waiter_probe(waiter)
                _remove_waiter(self._waiters, waiter)
                return WaitOutcome(held, sock.raw_states.get(sandbox_id, ""))
            if verdict == "reject":
                self._cancel_waiter_probe(waiter)
                _remove_waiter(self._waiters, waiter)
                # The wire status the hub holds with the state, as TypeScript.
                raise SandboxTerminalStateError(
                    sandbox_id, held, raw_state=sock.raw_states.get(sandbox_id) or None
                )
        waiter.registered_at = _clock.monotonic()
        # The newest waiter's start: a fresh wait (a start() next to an old
        # background wait, say) gets the young schedule, not the old wait's.
        sock.waited_since[sandbox_id] = waiter.registered_at

        if sandbox_id not in sock.subscriptions:
            sock.subscriptions.add(sandbox_id)
            self._enqueue_subscribe_ids(sock, [sandbox_id])

        if sandbox_poll_fn is not None and self._early_probe_s > 0:
            initial_probe_delay_s = self._initial_probe_delay_s(waiter)
            self._schedule_early_probe(
                waiter,
                sandbox_poll_fn,
                initial_probe_delay_s,
                enforce_probe=initial_probe_delay_s < self._early_probe_s,
            )

        self._ensure_socket_task(sock)
        sock.wake.set()
        if sock.ws is not None:
            self._ensure_stale_sweep(sock)

        try:
            rem = _remaining()
            if rem is not None:
                if rem <= 0:
                    self._cancel_waiter_probe(waiter)
                    _remove_waiter(self._waiters, waiter)
                    self._maybe_unsubscribe_for_id(sandbox_id)
                    raise RequestTimeoutError(
                        f"sandbox {sandbox_id} did not reach the requested state before timeout",
                        status_code=408,
                        code="REQUEST_TIMEOUT",
                        source="SDK",
                    )
                return await asyncio.wait_for(future, timeout=rem)
            return await future
        except asyncio.TimeoutError:
            self._cancel_waiter_probe(waiter)
            _remove_waiter(self._waiters, waiter)
            # The socket that holds the id now: a learned endpoint may have
            # moved it since this wait registered.
            self._maybe_unsubscribe_for_id(sandbox_id)
            raise RequestTimeoutError(
                f"sandbox {sandbox_id} did not reach the requested state before timeout",
                status_code=408,
                code="REQUEST_TIMEOUT",
                source="SDK",
            )
        except _HubFallback:
            self._cancel_waiter_probe(waiter)
            _remove_waiter(self._waiters, waiter)
            return await _use_fallback()
        except asyncio.CancelledError:
            self._cancel_waiter_probe(waiter)
            _remove_waiter(self._waiters, waiter)
            self._maybe_unsubscribe_for_id(sandbox_id)
            raise

    # ------------------------------------------------------------------
    # Fallback long-poll
    # ------------------------------------------------------------------

    async def _fallback_wait(
        self,
        sandbox_id: str,
        predicate: _Predicate,
        *,
        timeout: Optional[float],
    ) -> WaitOutcome:
        """Long-poll fallback on the hub's own transport: ``GET /sandboxes/{id}``
        with ``wait=`` when the predicate names one target."""
        deadline = None if (timeout is None or timeout == 0) else _clock.monotonic() + timeout
        wait_wire = _predicate_fallback_wait_state(predicate)
        path = f"/sandboxes/{quote(sandbox_id, safe='')}"

        while True:
            remaining = None if deadline is None else deadline - _clock.monotonic()
            if remaining is not None and remaining <= 0:
                raise RequestTimeoutError(
                    f"sandbox {sandbox_id} did not reach the requested state before timeout",
                    status_code=408,
                    code="REQUEST_TIMEOUT",
                    source="SDK",
                )
            slice_ms = (
                _FALLBACK_SLICE_MS
                if remaining is None
                else min(_FALLBACK_SLICE_MS, max(1, math.ceil(remaining * 1000)))
            )
            params: Dict[str, Union[str, int]] = {}
            if wait_wire is not None:
                params[wait_query_key(wait_wire, predicate.terminal_states)] = wait_wire
                params["timeout_ms"] = slice_ms
            poll_start = _clock.monotonic()
            data = expect_object(
                await self._api.send(
                    RequestSpec(
                        "GET",
                        path,
                        params=dict(params),
                        timeout=None if remaining is None else slice_ms / 1000 + 5,
                        unbounded_read=remaining is None,
                    )
                )
            )
            raw = str(data.get("status") or "")
            state = sandbox_state(raw)
            result = _evaluate(predicate, state, sandbox_id)
            if result == "resolve":
                return WaitOutcome(state, raw)
            if result == "reject":
                reason = data.get("error")
                raise SandboxTerminalStateError(
                    sandbox_id, state, raw_state=raw, reason=reason if isinstance(reason, str) else None
                )
            elapsed = _clock.monotonic() - poll_start
            if elapsed < _FALLBACK_MIN_INTERVAL_S:
                pause = _FALLBACK_MIN_INTERVAL_S - elapsed
                if deadline is not None:
                    pause = min(pause, max(0.0, deadline - _clock.monotonic()))
                if pause > 0:
                    await _sleep(pause)

    def _find_socket_for_sandbox(self, sandbox_id: str) -> Optional[_SocketState]:
        for sock in self._sockets.values():
            if sandbox_id in sock.subscriptions:
                return sock
        return None

    def _schedule_early_probe(
        self,
        waiter: _Waiter,
        sandbox_poll_fn: Callable[[], Awaitable[WaitOutcome]],
        delay_s: float,
        *,
        enforce_probe: bool = False,
    ) -> None:
        waiter.early_probe_task = asyncio.get_running_loop().create_task(
            self._run_early_probe_after_delay(waiter, sandbox_poll_fn, delay_s, enforce_probe)
        )

    def _reschedule_after_not_ready_poll(
        self,
        waiter: _Waiter,
        sandbox_poll_fn: Callable[[], Awaitable[WaitOutcome]],
    ) -> None:
        sock = self._find_socket_for_sandbox(waiter.sandbox_id)
        if sock is None:
            return
        silent_s = _clock.monotonic() - max(sock.last_frame_at, waiter.registered_at)
        self._schedule_early_probe(
            waiter,
            sandbox_poll_fn,
            max(max(0.0, self._early_probe_s - silent_s), WAIT_FOR_STATE_MIN_INTERVAL_S),
        )

    async def _run_early_probe_after_delay(
        self,
        waiter: _Waiter,
        sandbox_poll_fn: Callable[[], Awaitable[WaitOutcome]],
        delay_s: float,
        enforce_probe: bool,
    ) -> None:
        current = asyncio.current_task()
        try:
            await _sleep(_jittered(max(0.0, delay_s)))
            if self._waiter_still_pending(waiter):
                await self._run_early_probe(waiter, sandbox_poll_fn, enforce_probe=enforce_probe)
        finally:
            if waiter.early_probe_task is current:
                waiter.early_probe_task = None

    async def _run_early_probe(
        self,
        waiter: _Waiter,
        sandbox_poll_fn: Callable[[], Awaitable[WaitOutcome]],
        *,
        enforce_probe: bool = False,
    ) -> None:
        if not self._waiter_still_pending(waiter):
            return
        sock = self._find_socket_for_sandbox(waiter.sandbox_id)
        if sock is None:
            self._schedule_early_probe(waiter, sandbox_poll_fn, self._early_probe_s)
            return
        last_relevant_at = max(sock.last_frame_at, waiter.registered_at)
        silent_s = _clock.monotonic() - last_relevant_at
        quiet_enough = (
            sock.last_frame_at <= waiter.registered_at if enforce_probe else silent_s >= self._early_probe_s
        )
        if not quiet_enough:
            self._schedule_early_probe(waiter, sandbox_poll_fn, max(0.0, self._early_probe_s - silent_s))
            return
        before_poll = sock.last_frame_at
        try:
            outcome = await sandbox_poll_fn()
        except Exception as exc:
            if not self._waiter_still_pending(waiter):
                return
            if isinstance(exc, (NotFoundError, AuthenticationError, ForbiddenError)):
                self._cancel_waiter_probe(waiter)
                _remove_waiter(self._waiters, waiter)
                self._maybe_unsubscribe_for_id(waiter.sandbox_id)
                if not waiter.future.done():
                    waiter.future.set_exception(exc)
                return
            self._schedule_early_probe(waiter, sandbox_poll_fn, self._early_probe_s)
            return
        if not self._waiter_still_pending(waiter):
            return
        sock = self._find_socket_for_sandbox(waiter.sandbox_id)
        if sock is None:
            self._schedule_early_probe(waiter, sandbox_poll_fn, self._early_probe_s)
            return
        if sock.last_frame_at != before_poll:
            silent_s = _clock.monotonic() - max(sock.last_frame_at, waiter.registered_at)
            self._schedule_early_probe(waiter, sandbox_poll_fn, max(0.0, self._early_probe_s - silent_s))
            return
        result = _evaluate(waiter.predicate, outcome.state, waiter.sandbox_id)
        if result == "keep":
            self._reschedule_after_not_ready_poll(waiter, sandbox_poll_fn)
            return
        self._cancel_waiter_probe(waiter)
        _remove_waiter(self._waiters, waiter)
        if result == "resolve":
            if not waiter.future.done():
                waiter.future.set_result(outcome)
        else:
            if not waiter.future.done():
                waiter.future.set_exception(
                    SandboxTerminalStateError(waiter.sandbox_id, outcome.state, raw_state=outcome.raw_state)
                )
        self._maybe_unsubscribe_for_id(waiter.sandbox_id)

    def _settle_waiter_from_observed_outcome(self, waiter: _Waiter, outcome: WaitOutcome) -> None:
        if waiter.observe is not None:
            waiter.observe(outcome)
        result = _evaluate(waiter.predicate, outcome.state, waiter.sandbox_id)
        if result == "keep":
            return
        self._cancel_waiter_probe(waiter)
        _remove_waiter(self._waiters, waiter)
        if result == "resolve":
            if not waiter.future.done():
                waiter.future.set_result(outcome)
        else:
            if not waiter.future.done():
                waiter.future.set_exception(
                    SandboxTerminalStateError(
                        waiter.sandbox_id,
                        outcome.state,
                        raw_state=outcome.raw_state,
                    )
                )
        self._maybe_unsubscribe_for_id(waiter.sandbox_id)

    # ------------------------------------------------------------------
    # Socket loop (one asyncio Task per socket key)
    # ------------------------------------------------------------------

    async def _socket_loop(self, sock: _SocketState) -> None:
        """One socket's lifetime: connect when a waiter needs it, run until the
        socket drops (or idles out under ``idle_close``), reconnect with
        full-jitter backoff, fall back after repeated failures."""
        while not self._closed:
            if not self._has_any_waiter_for(sock):
                sock.wake.clear()
                await sock.wake.wait()
                sock.reconnect_attempt = 0
                continue
            try:
                await self._connect_and_run(sock)
                sock.reconnect_attempt = 0
                sock.credential_refusals = 0
            except asyncio.CancelledError:
                break
            except _AuthError as exc:
                sock.credential_refusals = 0
                self._fail_socket_waiters(sock, exc.error)
                break
            except _PermanentFallbackError:
                sock.permanent_fallback = True
                await self._activate_permanent_fallback(sock)
                break
            except _ReconnectError as exc:
                if not exc.credential_refused:
                    # Any failure that is not a credential refusal breaks the
                    # run: the counter must mean "in a row".
                    sock.credential_refusals = 0
                if exc.credential_refused:
                    # 4401: the credential behind the ticket is gone or the
                    # lease failed. A fresh ticket is the right answer to the
                    # first one — after a backoff. If the mint keeps
                    # succeeding and the socket keeps being refused, minting
                    # again as fast as the loop allows is a hot loop against
                    # the API, so tell the callers instead.
                    sock.credential_refusals += 1
                    if sock.credential_refusals >= _MAX_CREDENTIAL_REFUSALS:
                        self._fail_socket_waiters(
                            sock,
                            AuthenticationError(
                                "the credential was refused by the events socket; mint a new one and retry",
                                status_code=401,
                                code="UNAUTHORIZED",
                                source="SDK",
                            ),
                        )
                        break
                    await _sleep(_backoff(sock.credential_refusals - 1))
                    continue
                await self._after_failure(sock)
            except Exception:
                sock.credential_refusals = 0
                await self._after_failure(sock)
        sock.ws = None

    async def _after_failure(self, sock: _SocketState) -> None:
        sock.consecutive_failures += 1
        if sock.consecutive_failures >= _TRANSIENT_FAILURE_THRESHOLD:
            sock.fallback_until = _clock.monotonic() + _FALLBACK_RETRY_S
            self._notify_fallback_active(sock)
            await _sleep(_FALLBACK_RETRY_S)
            sock.fallback_until = None
            sock.consecutive_failures = 0
            return
        await _sleep(_backoff(sock.reconnect_attempt))
        sock.reconnect_attempt += 1

    async def _connect_and_run(self, sock: _SocketState) -> None:
        """Mint a ticket, open WS, resubscribe, run frame loop."""
        ticket, _ = await self._mint_ticket(sock)
        uri = _ws_url(self._api_url, sock.region)
        subprotocols = [_SUBPROTOCOL, f"{_TICKET_PROTOCOL_PREFIX}{ticket}"]

        ws = await self._connector(uri, subprotocols)
        sock.ws = ws
        self._clear_pending_subscribe_ids(sock)
        sock.last_frame_at = _clock.monotonic()
        sock.client_ping_sent_for_frame_at = None
        # The failure count resets on the first frame (`_run_ws`), not here:
        # a server that answers 101 and then says nothing must still reach
        # the fallback threshold.

        # Start reconcile timer if configured
        ri = self._config.reconcile_interval
        if ri and ri > 0 and (sock.reconcile_task is None or sock.reconcile_task.done()):
            loop = asyncio.get_event_loop()
            sock.reconcile_task = loop.create_task(self._reconcile_loop(sock))
        self._ensure_stale_sweep(sock)

        try:
            await self._resubscribe_all(sock)
            await self._run_ws(sock, ws)
        finally:
            self._invalidate_socket_cache(sock)
            sock.ws = None
            self._clear_pending_subscribe_ids(sock)
            if sock.reconcile_task is not None and not sock.reconcile_task.done():
                sock.reconcile_task.cancel()
            if sock.stale_task is not None and not sock.stale_task.done():
                sock.stale_task.cancel()
            with contextlib.suppress(Exception):
                await ws.close()

    def _invalidate_socket_cache(self, sock: _SocketState) -> None:
        for sandbox_id in list(sock.subscriptions):
            sock.states.pop(sandbox_id, None)
            sock.raw_states.pop(sandbox_id, None)

    async def _mint_ticket(self, sock: _SocketState) -> Tuple[str, str]:
        """``POST /events/{region}/ticket``. A 404 selects long-poll fallback;
        401/403 are the caller's auth error;
        anything else is a reconnect."""
        path = f"/events/{quote(sock.region, safe='')}/ticket"
        try:
            # One 30 s budget for the whole mint, a token exchange included.
            with _request_deadline(_TICKET_MINT_SECONDS):
                data = expect_object(
                    await self._api.send(RequestSpec("POST", path, timeout=_TICKET_MINT_SECONDS))
                )
        except NotFoundError as exc:
            raise _PermanentFallbackError() from exc
        except (AuthenticationError, ForbiddenError) as exc:
            raise _AuthError(exc) from exc
        except DaytonaError as exc:
            raise _ReconnectError(f"ticket request failed: {exc}") from exc
        ticket = str(data.get("ticket") or "")
        if not ticket:
            raise _ReconnectError("ticket response missing ticket field")
        return ticket, str(data.get("subprotocol") or _SUBPROTOCOL)

    async def _run_ws(self, sock: _SocketState, ws: _WsConn) -> None:
        """Read and dispatch frames until the socket closes. The socket stays
        open with no waiters unless ``idle_close`` says otherwise."""
        # `websockets` is a hard dependency (pyproject), so the close code is
        # always available: a 4401 is never mistaken for a transient failure.
        import websockets.exceptions as _ws_exc  # type: ignore[import-untyped]

        idle_close = self._config.idle_close
        while not self._closed:
            wait_for: Optional[float] = None
            has_waiters = self._has_any_waiter_for(sock)
            if idle_close is not None and idle_close > 0 and not has_waiters:
                if sock.idle_since is None:
                    sock.idle_since = _clock.monotonic()
                wait_for = max(0.0, sock.idle_since + idle_close - _clock.monotonic())
                if wait_for == 0.0:
                    return
            if has_waiters:
                ping_after_s = self._client_ping_after_s
                if ping_after_s > 0 and sock.client_ping_sent_for_frame_at != sock.last_frame_at:
                    client_ping_remaining = sock.last_frame_at + ping_after_s - _clock.monotonic()
                    wait_for = (
                        min(wait_for, client_ping_remaining)
                        if wait_for is not None
                        else client_ping_remaining
                    )
                no_frame_remaining = sock.last_frame_at + _NO_FRAME_TIMEOUT_S - _clock.monotonic()
                if no_frame_remaining <= 0:
                    raise _ReconnectError("WebSocket liveness timeout")
                wait_for = min(wait_for, no_frame_remaining) if wait_for is not None else no_frame_remaining
            try:
                msg = (
                    await ws.recv()
                    if wait_for is None
                    else await asyncio.wait_for(ws.recv(), timeout=wait_for)
                )
            except asyncio.TimeoutError:
                if not self._has_any_waiter_for(sock):
                    return
                if (
                    self._client_ping_after_s > 0
                    and sock.client_ping_sent_for_frame_at != sock.last_frame_at
                    and sock.last_frame_at + self._client_ping_after_s <= _clock.monotonic()
                ):
                    try:
                        await ws.send(json.dumps({"type": "ping"}))
                    except Exception as exc:
                        raise _ReconnectError(f"WS send error: {exc}") from exc
                    sock.client_ping_sent_for_frame_at = sock.last_frame_at
                    continue
                if (
                    self._has_any_waiter_for(sock)
                    and sock.last_frame_at + _NO_FRAME_TIMEOUT_S <= _clock.monotonic()
                ):
                    raise _ReconnectError("WebSocket liveness timeout")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, _ws_exc.ConnectionClosed):
                    code = getattr(getattr(exc, "rcvd", None), "code", None)
                    raise _ReconnectError(f"WS closed: {exc}", credential_refused=code == 4401) from exc
                raise _ReconnectError(f"WS recv error: {exc}") from exc

            sock.consecutive_failures = 0
            sock.credential_refusals = 0
            sock.reconnect_attempt = 0
            sock.last_frame_at = _clock.monotonic()
            sock.client_ping_sent_for_frame_at = None
            try:
                # A frame nested too deeply to decode is skipped like any
                # undecodable one, never a RecursionError out of the hub.
                frame = loads_json(msg, "sandbox events frame")
            except (ValueError, TypeError, DaytonaError):
                continue
            if not isinstance(frame, dict):
                continue
            ftype = frame.get("type")

            if ftype == "ping":
                with contextlib.suppress(Exception):
                    await ws.send(json.dumps({"type": "pong"}))
                continue
            if ftype == "pong":
                continue

            if ftype in ("snapshot", "event"):
                sid = frame.get("id")
                version = frame.get("version")
                if not isinstance(sid, str) or not isinstance(version, int):
                    continue
                self._remember_known(sid)
                pending_version = sock.snapshot_pending.get(sid)
                if ftype == "event" and pending_version is not None and version <= pending_version:
                    continue
                if ftype == "snapshot" or pending_version is not None:
                    sock.snapshot_pending.pop(sid, None)
                if version < sock.versions.get(sid, -1):
                    continue
                if sid not in sock.subscriptions:
                    continue
                sock.versions[sid] = version
                sock.heard_at[sid] = _clock.monotonic()
                raw = str(frame.get("status") or "")
                decoded = sandbox_state(raw)
                sock.states[sid] = decoded
                sock.raw_states[sid] = raw
                self._dispatch_state(sock, sid, WaitOutcome(decoded, raw))
            elif ftype == "error":
                sid = frame.get("id")
                code = frame.get("code")
                if code == "LIMIT" and sid is None:
                    self._retry_socket_level_limit(sock)
                    continue
                if not isinstance(sid, str):
                    continue
                if sid not in sock.subscriptions:
                    continue
                if code == "NOT_FOUND":
                    self._dispatch_not_found(sock, sid, was_known=bool(frame.get("was_known", False)))
                elif code == "LIMIT":
                    # The server refused this id and will send no snapshot
                    # for it; waiting for one is waiting forever. Retry it
                    # after a pause, which is what the frame means.
                    self._retry_refused(sock, sid)
                elif code == "MALFORMED":
                    # The id itself does not parse: retrying cannot help.
                    self._fail_waiters(
                        sid,
                        create_error(
                            f"the server rejected sandbox id {sid!r} as malformed",
                            400,
                            "BAD_REQUEST",
                            "SDK",
                        ),
                    )
                    # Release the optimistic local subscription too, or the
                    # next wait on this id sees it as already subscribed,
                    # sends no subscribe, and waits until its timeout.
                    self._release_subscription(sock, sid)

    # ------------------------------------------------------------------
    # Frame dispatch helpers
    # ------------------------------------------------------------------

    def _dispatch_state(self, sock: _SocketState, sandbox_id: str, outcome: WaitOutcome) -> None:
        """Resolve or reject waiters based on the new state."""
        state = outcome.state
        waiters = self._waiters.get(sandbox_id)
        if not waiters:
            return

        to_resolve: List[_Waiter] = []
        to_reject: List[Tuple[_Waiter, Exception]] = []

        for w in list(waiters):
            if w.observe is not None:
                w.observe(outcome)
            result = _evaluate(w.predicate, state, sandbox_id)
            if result == "resolve":
                to_resolve.append(w)
            elif result == "reject":
                err = SandboxTerminalStateError(sandbox_id, state, raw_state=outcome.raw_state)
                to_reject.append((w, err))

        for w in to_resolve:
            self._cancel_waiter_probe(w)
            if not w.future.done():
                w.future.set_result(outcome)
            _remove_waiter(self._waiters, w)

        for w, err in to_reject:
            self._cancel_waiter_probe(w)
            if not w.future.done():
                w.future.set_exception(err)
            _remove_waiter(self._waiters, w)

        # Unsubscribe if no more waiters
        if not self._waiters.get(sandbox_id):
            self._maybe_unsubscribe(sock, sandbox_id)

    def _dispatch_not_found(self, sock: _SocketState, sandbox_id: str, *, was_known: bool) -> None:
        """Handle NOT_FOUND error frame."""
        region = region_of(sandbox_id)
        endpoint = self._learned.get(region)
        # Was this id assigned to a discovered service endpoint?
        discovered_endpoint = endpoint is not None and sock.key == endpoint

        if discovered_endpoint:
            # Contradiction: a real id from a real response is unknown to the
            # discovered endpoint. Evict the entry and retry once by region.
            del self._learned[region]
            self._release_subscription(sock, sandbox_id)
            asyncio.get_running_loop().create_task(self._retry_via_region(sandbox_id, region))
            return

        # No discovered endpoint: apply was_known semantics.
        if was_known or sandbox_id in self._known:
            # Terminal with unrecorded outcome — reject target waits
            self._fail_waiters(
                sandbox_id,
                NotFoundError(
                    f"sandbox {sandbox_id} is no longer present; its final state was not retained",
                    status_code=404,
                    code="NOT_FOUND",
                    source="SDK",
                ),
            )
            self._release_subscription(sock, sandbox_id)
            return

        self._fail_waiters(sandbox_id, _HubFallback())
        self._release_subscription(sock, sandbox_id)

    def _retry_refused(self, sock: _SocketState, sandbox_id: str) -> None:
        """Re-subscribe an id a budget refused, after a pause."""
        if not self._waiters.get(sandbox_id):
            return
        sock.refused_retry_ids.add(sandbox_id)
        if sock.refused_retry_task is None or sock.refused_retry_task.done():
            sock.refused_retry_task = asyncio.get_running_loop().create_task(
                self._resubscribe_refused_batch(sock)
            )

    def _retry_socket_level_limit(self, sock: _SocketState) -> None:
        for sandbox_id in sock.subscriptions:
            if not self._waiters.get(sandbox_id):
                continue
            sock.refused_retry_ids.add(sandbox_id)
        if sock.refused_retry_ids and (sock.refused_retry_task is None or sock.refused_retry_task.done()):
            sock.refused_retry_task = asyncio.get_running_loop().create_task(
                self._resubscribe_refused_batch(sock)
            )

    def _enqueue_subscribe_ids(self, sock: _SocketState, ids: List[str]) -> None:
        if not ids:
            return
        for sandbox_id in ids:
            if sandbox_id in sock.pending_unsubscribe_ids:
                sock.pending_unsubscribe_ids.discard(sandbox_id)
                sock.unsubscribe_replaced_ids.add(sandbox_id)
            sock.pending_subscribe_ids.add(sandbox_id)
        if sock.subscribe_flush_task is None or sock.subscribe_flush_task.done():
            sock.subscribe_flush_task = asyncio.get_running_loop().create_task(
                self._flush_pending_subscription_ids(sock)
            )

    def _enqueue_unsubscribe_ids(self, sock: _SocketState, ids: List[str]) -> None:
        if not ids:
            return
        for sandbox_id in ids:
            if sandbox_id in sock.pending_subscribe_ids:
                sock.pending_subscribe_ids.discard(sandbox_id)
                # Never sent, so nothing to undo — unless it had replaced a
                # pending unsubscribe for an id the server still holds.
                if sandbox_id not in sock.unsubscribe_replaced_ids:
                    continue
                sock.unsubscribe_replaced_ids.discard(sandbox_id)
            sock.pending_unsubscribe_ids.add(sandbox_id)
        if sock.subscribe_flush_task is None or sock.subscribe_flush_task.done():
            sock.subscribe_flush_task = asyncio.get_running_loop().create_task(
                self._flush_pending_subscription_ids(sock)
            )

    async def _flush_pending_subscription_ids(self, sock: _SocketState) -> None:
        try:
            await _sleep(self._subscribe_flush_s)
            if self._closed:
                sock.pending_subscribe_ids.clear()
                sock.pending_unsubscribe_ids.clear()
                sock.unsubscribe_replaced_ids.clear()
                return

            subscribe_ids = [
                sandbox_id
                for sandbox_id in sock.pending_subscribe_ids
                if self._waiters.get(sandbox_id) and sandbox_id in sock.subscriptions
            ]
            # Unsubscribe every pending id this socket no longer wants: no
            # waiter, or its waiter now listens on another socket. A dropped
            # re-subscribe that had replaced an unsubscribe counts too — the
            # server still holds the id.
            resent = set(subscribe_ids)
            unsubscribe_ids = [
                sandbox_id
                for sandbox_id in sock.pending_unsubscribe_ids | sock.unsubscribe_replaced_ids
                if sandbox_id not in resent
                and not (self._waiters.get(sandbox_id) and sandbox_id in sock.subscriptions)
            ]
            sock.pending_subscribe_ids.clear()
            sock.pending_unsubscribe_ids.clear()
            sock.unsubscribe_replaced_ids.clear()
            # Release the task handle BEFORE awaiting the sends: `send()` yields
            # while the write buffer is above its high-water mark, and ids
            # enqueued meanwhile must schedule a flush of their own instead of
            # waiting on this one (which has already read the sets).
            if sock.subscribe_flush_task is asyncio.current_task():
                sock.subscribe_flush_task = None
            if sock.ws is None:
                return
            async with sock.flush_send_lock:
                if subscribe_ids:
                    await self._send_subscribe_ids(sock, subscribe_ids)
                if unsubscribe_ids:
                    await self._send_unsubscribe_ids(sock, unsubscribe_ids)
        finally:
            with contextlib.suppress(RuntimeError):
                if sock.subscribe_flush_task is asyncio.current_task():
                    sock.subscribe_flush_task = None

    def _clear_pending_subscribe_ids(self, sock: _SocketState) -> None:
        if sock.subscribe_flush_task is not None and not sock.subscribe_flush_task.done():
            sock.subscribe_flush_task.cancel()
        sock.subscribe_flush_task = None
        sock.pending_subscribe_ids.clear()
        sock.pending_unsubscribe_ids.clear()
        sock.unsubscribe_replaced_ids.clear()

    async def _resubscribe_refused_batch(self, sock: _SocketState) -> None:
        await _sleep(_REFUSED_RETRY_S)
        sock.refused_retry_task = None
        if self._closed:
            sock.refused_retry_ids.clear()
            return
        retry_ids: list[str] = []
        for sandbox_id in list(sock.refused_retry_ids):
            if not self._waiters.get(sandbox_id) or sandbox_id not in sock.subscriptions:
                sock.refused_retry_ids.discard(sandbox_id)
                continue
            if len(retry_ids) >= _MAX_IDS_PER_FRAME:
                continue
            sock.refused_retry_ids.discard(sandbox_id)
            retry_ids.append(sandbox_id)
        if retry_ids and sock.ws is not None:
            await self._send_subscribe_ids(sock, retry_ids)
        for sandbox_id in list(sock.refused_retry_ids):
            if not self._waiters.get(sandbox_id) or sandbox_id not in sock.subscriptions:
                sock.refused_retry_ids.discard(sandbox_id)
        if sock.refused_retry_ids:
            sock.refused_retry_task = asyncio.get_running_loop().create_task(
                self._resubscribe_refused_batch(sock)
            )

    def _fail_waiters(self, sandbox_id: str, error: Exception) -> None:
        for w in list(self._waiters.pop(sandbox_id, [])):
            self._cancel_waiter_probe(w)
            if not w.future.done():
                w.future.set_exception(error)

    async def _retry_via_region(self, sandbox_id: str, region: str) -> None:
        """Retry subscription on a region-keyed socket after endpoint eviction."""
        if not self._waiters.get(sandbox_id):
            return
        sock = self._ensure_socket(region, region)
        sock.subscriptions.add(sandbox_id)
        self._ensure_socket_task(sock)
        sock.wake.set()
        if sock.ws is not None:
            await self._send_subscribe_ids(sock, [sandbox_id])

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe helpers
    # ------------------------------------------------------------------

    async def _send_subscribe_ids(self, sock: _SocketState, ids: List[str]) -> None:
        if sock.ws is None:
            return
        now = _clock.monotonic()
        for sandbox_id in ids:
            sock.snapshot_pending[sandbox_id] = sock.versions.get(sandbox_id, -1)
            sock.heard_at[sandbox_id] = now
        for chunk in _chunks(ids, _MAX_IDS_PER_FRAME):
            frame = {
                "type": "subscribe",
                "ids": [{"id": sid, "known": sid in self._known} for sid in chunk],
            }
            with contextlib.suppress(Exception):
                await sock.ws.send(json.dumps(frame))

    async def _send_unsubscribe_ids(self, sock: _SocketState, ids: List[str]) -> None:
        if sock.ws is None:
            return
        for chunk in _chunks(ids, _MAX_IDS_PER_FRAME):
            frame = {"type": "unsubscribe", "ids": chunk}
            with contextlib.suppress(Exception):
                await sock.ws.send(json.dumps(frame))

    async def _resubscribe_all(self, sock: _SocketState) -> None:
        """Send subscribe frames for every id that still has a live waiter."""
        ids = [sid for sid in sock.subscriptions if self._waiters.get(sid)]
        if ids:
            await self._send_subscribe_ids(sock, ids)

    def _maybe_unsubscribe_for_id(self, sandbox_id: str) -> None:
        for sock in self._sockets.values():
            if sandbox_id in sock.subscriptions:
                self._maybe_unsubscribe(sock, sandbox_id)
                break

    def _maybe_unsubscribe(self, sock: _SocketState, sandbox_id: str) -> None:
        """Unsubscribe sandbox_id if no more waiters for it."""
        if self._waiters.get(sandbox_id):
            return  # still has waiters
        self._release_subscription(sock, sandbox_id)

    def _release_subscription(self, sock: _SocketState, sandbox_id: str) -> None:
        sock.subscriptions.discard(sandbox_id)
        sock.heard_at.pop(sandbox_id, None)
        sock.waited_since.pop(sandbox_id, None)
        sock.states.pop(sandbox_id, None)
        sock.raw_states.pop(sandbox_id, None)
        sock.versions.pop(sandbox_id, None)
        sock.snapshot_pending.pop(sandbox_id, None)
        sock.refused_retry_ids.discard(sandbox_id)
        self._enqueue_unsubscribe_ids(sock, [sandbox_id])
        if not self._has_any_waiter_for(sock):
            sock.idle_since = _clock.monotonic()

    # ------------------------------------------------------------------
    # Reconcile timer
    # ------------------------------------------------------------------

    async def _reconcile_loop(self, sock: _SocketState) -> None:
        interval = self._config.reconcile_interval or 60.0
        while not self._closed:
            delay = _jittered(interval)
            await _sleep(delay)
            if sock.ws is not None:
                await self._resubscribe_all(sock)

    def _ensure_stale_sweep(self, sock: _SocketState) -> None:
        """Run the stale sweep on a connected socket; it stops by itself once
        the socket has no waiter, and a new waiter starts it again."""
        if self._stale_sweep_tick_s > 0 and (sock.stale_task is None or sock.stale_task.done()):
            sock.stale_task = asyncio.get_running_loop().create_task(self._stale_resubscribe_loop(sock))

    def _stale_sweep_cap(self) -> int:
        """Ids one tick re-subscribes at most: the rate cap over this tick."""
        if self._stale_sweep_max_ids is not None:
            return self._stale_sweep_max_ids
        return max(1, int(_STALE_SWEEP_IDS_PER_S * self._stale_sweep_tick_s))

    def _is_stale(self, sock: _SocketState, sandbox_id: str, now: float) -> bool:
        """Whether ``sandbox_id`` should be asked again: it has heard nothing
        for its stale threshold (half its wait's age, ``_stale_after_min_s``
        up to the ceiling; the ceiling when the wait's start is unknown), and
        no answer to a recent subscribe of it is still outstanding.

        The ceiling is ``_stale_after_s``, or ``_stale_after_slow_s`` once the
        wait is older than ``_stale_slow_after_s``. It is per id: one id's
        events say nothing about whether another id's event went missing."""
        heard = sock.heard_at.get(sandbox_id, 0.0)
        if sandbox_id in sock.snapshot_pending and heard > now - self._stale_after_s:
            # Its subscribe went out recently and the answer is still due:
            # asking again would only add load where the server is slowest.
            return False
        since = sock.waited_since.get(sandbox_id)
        high = (
            self._stale_after_slow_s
            if since is not None and now - since > self._stale_slow_after_s
            else self._stale_after_s
        )
        threshold = high if since is None else min(high, max(self._stale_after_min_s, (now - since) / 2))
        return heard <= now - threshold

    async def _stale_resubscribe_loop(self, sock: _SocketState) -> None:
        """Re-subscribe every waited-on id that has heard nothing for its
        stale threshold (:meth:`_is_stale`).

        A wait whose transition event never arrives would otherwise end only
        at the reconcile timer. The frame path pays one dict store; a tick
        scans the subscriptions and sends nothing while every id is fresh.
        """
        while not self._closed:
            await _sleep(_jittered(self._stale_sweep_tick_s))
            if not self._has_any_waiter_for(sock):
                return
            if sock.ws is None:
                continue
            heard_at = sock.heard_at
            now = _clock.monotonic()
            if not any(self._is_stale(sock, sid, now) for sid in sock.subscriptions):
                continue
            # Under the flush lock, so a flush's unsubscribe of an id that
            # leaves meanwhile cannot overtake this subscribe of it.
            async with sock.flush_send_lock:
                pending = sock.pending_subscribe_ids
                refused = sock.refused_retry_ids
                # Ids already queued for a subscribe, or for the refused
                # (LIMIT) retry, are asked again by those paths.
                stale = [
                    sid
                    for sid in sock.subscriptions
                    if sid not in pending
                    and sid not in refused
                    and self._is_stale(sock, sid, now)
                    and self._waiters.get(sid)
                ]
                max_ids = self._stale_sweep_cap()
                if len(stale) > max_ids:
                    stale.sort(key=lambda sid: heard_at.get(sid, 0.0))
                    del stale[max_ids:]
                if stale and sock.ws is not None:
                    await self._send_subscribe_ids(sock, stale)

    # ------------------------------------------------------------------
    # Fallback helpers
    # ------------------------------------------------------------------

    async def _activate_permanent_fallback(self, sock: _SocketState) -> None:
        """Signal pending waiters that the socket is permanently unavailable."""
        err = _HubFallback()
        for sid in list(sock.subscriptions):
            for w in list(self._waiters.pop(sid, [])):
                self._cancel_waiter_probe(w)
                if not w.future.done():
                    w.future.set_exception(err)
        sock.subscriptions.clear()
        sock.heard_at.clear()
        sock.waited_since.clear()
        sock.states.clear()
        sock.raw_states.clear()
        sock.versions.clear()
        sock.snapshot_pending.clear()
        self._clear_pending_subscribe_ids(sock)
        sock.refused_retry_ids.clear()

    def _notify_fallback_active(self, sock: _SocketState) -> None:
        """Signal pending waiters that the socket has entered transient fallback."""
        err = _HubFallback()
        for sid in list(sock.subscriptions):
            for w in list(self._waiters.pop(sid, [])):
                self._cancel_waiter_probe(w)
                if not w.future.done():
                    w.future.set_exception(err)
        sock.subscriptions.clear()
        sock.heard_at.clear()
        sock.waited_since.clear()
        sock.states.clear()
        sock.raw_states.clear()
        sock.versions.clear()
        sock.snapshot_pending.clear()
        self._clear_pending_subscribe_ids(sock)
        sock.refused_retry_ids.clear()

    # ------------------------------------------------------------------
    # Auth-error helpers
    # ------------------------------------------------------------------

    def _fail_socket_waiters(self, sock: _SocketState, error: Exception) -> None:
        """Fail all waiters that belong to this socket."""
        for sid in list(sock.subscriptions):
            for w in list(self._waiters.pop(sid, [])):
                self._cancel_waiter_probe(w)
                if not w.future.done():
                    w.future.set_exception(error)
        sock.subscriptions.clear()
        sock.heard_at.clear()
        sock.waited_since.clear()
        sock.states.clear()
        sock.raw_states.clear()
        sock.versions.clear()
        sock.snapshot_pending.clear()
        self._clear_pending_subscribe_ids(sock)
        sock.refused_retry_ids.clear()

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _has_any_waiter_for(self, sock: _SocketState) -> bool:
        return any(bool(self._waiters.get(sid)) for sid in sock.subscriptions)


async def _run_poll_loop(
    sandbox_id: str,
    predicate: _Predicate,
    *,
    deadline: Optional[float],
    fn: Callable[[], Awaitable[WaitOutcome]],
) -> WaitOutcome:
    while True:
        remaining = None if deadline is None else deadline - _clock.monotonic()
        if remaining is not None and remaining <= 0:
            raise RequestTimeoutError(
                f"sandbox {sandbox_id} did not reach the requested state before timeout",
                status_code=408,
                code="REQUEST_TIMEOUT",
                source="SDK",
            )
        outcome = await fn()
        result = _evaluate(predicate, outcome.state, sandbox_id)
        if result == "resolve":
            return outcome
        if result == "reject":
            raise SandboxTerminalStateError(sandbox_id, outcome.state, raw_state=outcome.raw_state or None)


# ---------------------------------------------------------------------------
# Waiter utilities
# ---------------------------------------------------------------------------


def _remove_waiter(waiters: Dict[str, List[_Waiter]], w: _Waiter) -> None:
    lst = waiters.get(w.sandbox_id)
    if lst is None:
        return
    try:
        lst.remove(w)
    except ValueError:
        pass
    if not lst:
        del waiters[w.sandbox_id]


# ---------------------------------------------------------------------------
# Process-wide hub registry
# ---------------------------------------------------------------------------

_HUB_REGISTRY: WeakValueDictionary[Tuple[int, Tuple[object, ...]], WaitHub] = WeakValueDictionary()


def _hub_identity(
    api_url: str,
    api_key: str,
    target: Optional[str],
    config: Optional[SandboxEventsConfig],
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    request_timeout: Optional[float] = None,
) -> Tuple[object, ...]:
    """What a shared hub is keyed on: clients that differ in any of it (their
    body cap, or the request timeout the hub's token exchange copies) get
    their own hub, so each client's limits apply to its hub's requests."""
    effective = config if config is not None else SandboxEventsConfig()
    return (
        _normalize_hub_url_identity(api_url),
        api_key,
        (target or "").lower(),
        effective.enabled,
        effective.reconcile_interval,
        effective.idle_close,
        max_response_bytes,
        request_timeout,
    )


def attach_hub(
    api_url: str,
    api_key: str,
    target: Optional[str],
    *,
    shared: bool,
    source: str,
    config: Optional[SandboxEventsConfig],
    connector: Optional[Callable[[str, List[str]], Awaitable[_WsConn]]] = None,
    auth: Optional[ProjectTokenAuth] = None,
    identity: Optional[str] = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> WaitHub:
    key: Optional[Tuple[int, Tuple[object, ...]]] = None
    running_loop: asyncio.AbstractEventLoop | None = None
    if shared:
        try:
            running_loop = asyncio.get_running_loop()
            key = (
                id(running_loop),
                _hub_identity(
                    api_url,
                    identity or api_key,
                    target,
                    config,
                    max_response_bytes,
                    auth._request_timeout if auth is not None else None,
                ),
            )
        except RuntimeError:
            key = None
    else:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
    if key is not None:
        existing = _HUB_REGISTRY.get(key)
        if existing is not None and not existing._closed:
            existing._attach()
            return existing
    hub_auth = (
        ProjectTokenAuth(
            api_url=auth._api_url,
            jwt_token=auth._jwt_token,
            project_id=auth._project_id,
            source=source,
            otel=auth._otel,
            request_timeout=auth._request_timeout,
            max_response_bytes=max_response_bytes,
        )
        if auth is not None
        else None
    )
    hub = WaitHub(
        api_url,
        api_key,
        source=source,
        config=config,
        connector=connector,
        loop=running_loop,
        auth=hub_auth,
        max_response_bytes=max_response_bytes,
    )
    hub._attach()
    if key is not None:
        _HUB_REGISTRY[key] = hub
    return hub


async def detach_hub(
    hub: WaitHub,
    api_url: str,
    api_key: str,
    target: Optional[str],
    *,
    shared: bool,
) -> None:
    await hub._detach()
    if hub._closed and shared:
        loop_id = id(asyncio.get_running_loop())
        for key, registered in list(_HUB_REGISTRY.items()):
            if key[0] == loop_id and registered is hub:
                del _HUB_REGISTRY[key]
                break
