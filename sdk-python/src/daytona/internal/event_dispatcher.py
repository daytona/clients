# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from collections.abc import Awaitable
from typing import Any, Callable, cast
from urllib.parse import quote, urlparse

import socketio

logger = logging.getLogger(__name__)

# Handler receives (event_name, raw_data).
EventHandler = Callable[[str, Any], None]
AsyncEventHandler = Callable[[str, Any], Awaitable[None] | None]

# The server accepts at most this many ids per subscribe/unsubscribe message.
_SUBSCRIBE_CHUNK_SIZE = 100
# The gateway's wire contract lets one connection hold at most this many subscribed ids. A server with
# a lower limit rejects a subscribe past it with SUBSCRIPTION_LIMIT_EXCEEDED in its acknowledgement,
# which marks the ids rejected. Module-level so tests can lower it.
MAX_SUBSCRIPTIONS_PER_CONNECTION = 10_000
# Delivered to an id's handlers by the dispatcher itself, never by the server, when the server rejects
# the id's subscribe: its waits read now and fall back to polling.
SUBSCRIPTION_REJECTED_EVENT = "daytona:subscription-rejected"
# An unanswered subscribe acknowledgement is dropped after this long. Servers without explicit
# subscriptions never acknowledge and still send every event, so silence is not a rejection.
_ACK_TIMEOUT = 10.0

AckCallback = Callable[..., Any]


class AsyncEventDispatcher:
    """Async event dispatcher that connects to the Socket.IO notification gateway.

    The connection opens lazily with the first subscription and asks the server for explicit
    subscriptions: only events for resources that currently have a listener are sent. Servers
    without explicit subscriptions send all organization events, so events are still filtered
    by resource id on the client.
    """

    _api_url: str
    _token: str
    _organization_id: str | None
    _sio: socketio.AsyncClient | None
    _connected: bool
    _failed: bool
    _fail_error: str | None
    _listeners: dict[str, list[AsyncEventHandler]]
    _registered_events: set[str]
    _disconnect_task: asyncio.Task[None] | None
    _disconnect_generation: int
    _closed: bool
    _lock: asyncio.Lock

    _DISCONNECT_DELAY: float = 30.0
    _CLOSE_TIMEOUT: float = 5.0

    def __init__(
        self,
        api_url: str,
        token: str,
        organization_id: str | None = None,
        source: str | None = None,
        sdk_version: str | None = None,
    ):
        self._api_url = api_url
        self._token = token
        self._organization_id = organization_id
        self._source: str | None = source
        self._sdk_version: str | None = sdk_version
        self._sio = None
        self._connected = False
        self._failed = False
        self._fail_error = None
        self._closed = False
        self._listeners = {}
        # Ids the server holds, or will hold after the next connect, with the token each was served
        # under: at most MAX_SUBSCRIPTIONS_PER_CONNECTION. Insertion-ordered, so connects re-send them
        # in order. An acknowledgement only acts on the tokens it was sent with, so a late rejection of
        # an id's earlier subscribe leaves a newer subscription of the same id alone.
        self._served: dict[str, int] = {}
        self._serve_seq: int = 0
        # Ids whose subscribe the server rejected; unserved until their last listener goes.
        self._rejected: set[str] = set()
        # (deadline, callback) of subscribe acknowledgements still pending, oldest first.
        self._pending_acks: collections.deque[tuple[float, AckCallback]] = collections.deque()
        self._registered_events = set()
        self._disconnect_task = None
        self._connect_task: asyncio.Task[None] | None = None
        self._disconnect_generation = 0
        self._lock = asyncio.Lock()
        self._emit_tasks: set[asyncio.Task[None]] = set()
        # Serializes all subscribe/unsubscribe sends so per-id messages never overtake the full set.
        self._emit_lock: asyncio.Lock = asyncio.Lock()
        self._connection_epoch: int = 0

    def ensure_connected(self) -> None:
        """Idempotent: ensure a connection attempt is in progress or already established.

        Non-blocking. Creates a background task if not already connected and no
        attempt is currently running.
        """
        if self._closed or self._connected:
            return
        if self._connect_task is not None and not self._connect_task.done():
            return

        async def _connect() -> None:
            try:
                await self.connect()
            except Exception:
                pass  # Callers check is_connected when they need it

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass  # No event loop — will connect on first await
        else:
            self._connect_task = loop.create_task(_connect())

    async def connect(self, timeout: float = 5.0) -> None:
        """Establish the Socket.IO connection. Raises on failure."""
        async with self._lock:
            if self._closed or self._connected:
                return
            old_sio = self._sio
            self._sio = None

        if old_sio:
            await old_sio.disconnect()

        parsed = urlparse(self._api_url.rstrip("/"))
        origin = f"{parsed.scheme}://{parsed.netloc}"
        socketio_path = f"{parsed.path.rstrip('/')}/socket.io/"

        sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=1,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )

        async with self._lock:
            self._sio = sio

        dispatcher = self

        @sio.event  # type: ignore[misc]
        async def connect() -> None:  # pyright: ignore[reportUnusedFunction]
            # Subscriptions belong to the socket: send the whole set on every connect and reconnect.
            # Ids added from here on are sent by subscribe() because the dispatcher is now connected;
            # those sends wait for the emit lock, so they always go out after the full set.
            async with dispatcher._emit_lock:
                served = dict(dispatcher._served)
                dispatcher._connected = True
                dispatcher._failed = False
                dispatcher._fail_error = None
                dispatcher._connection_epoch += 1
                # The previous socket's pending acknowledgements were dropped with it.
                dispatcher._pending_acks.clear()
                for chunk in _chunks(list(served)):
                    await dispatcher._send_subscribe(sio, {i: served[i] for i in chunk})

        @sio.event  # type: ignore[misc]
        async def disconnect() -> None:  # pyright: ignore[reportUnusedFunction]
            dispatcher._connected = False

        @sio.event  # type: ignore[misc]
        async def connect_error(data: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            dispatcher._connected = False
            dispatcher._failed = True
            dispatcher._fail_error = f"WebSocket connection failed: {data}"

        # Re-register any events that were added before the socket was created
        async with self._lock:
            pending_events = list(self._registered_events)
            self._registered_events.clear()
            self._register_events(pending_events)

        params: list[str] = []
        if self._organization_id:
            params.append(f"organizationId={quote(self._organization_id, safe='')}")
        if self._source:
            params.append(f"source={quote(self._source, safe='')}")
        if self._sdk_version:
            params.append(f"sdkVersion={quote(self._sdk_version, safe='')}")
        params.append("subscriptionMode=explicit")
        connect_url = f"{origin}?{'&'.join(params)}"

        try:
            await asyncio.wait_for(
                sio.connect(
                    connect_url,
                    socketio_path=socketio_path,
                    auth={"token": self._token},
                    transports=["websocket"],
                    headers={},
                    wait=True,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            self._failed = True
            self._fail_error = "WebSocket connection timed out"
            raise ConnectionError(self._fail_error) from None
        except Exception as e:
            self._failed = True
            self._fail_error = f"WebSocket connection failed: {e}"
            raise ConnectionError(self._fail_error) from e

        if not self._listeners:
            self._schedule_delayed_disconnect()

    def subscribe(
        self,
        resource_id: str,
        handler: AsyncEventHandler,
        events: list[str],
    ) -> Callable[[], None]:
        """Subscribe to specific events for a resource.

        Args:
            resource_id: The ID of the resource (e.g. sandbox ID).
            handler: Callback receiving (event_name, raw_data).
            events: List of Socket.IO event names to listen for.

        Returns:
            Unsubscribe function.
        """
        if self._closed:
            return lambda: None

        self.ensure_connected()

        self._disconnect_generation += 1

        if self._disconnect_task and not self._disconnect_task.done():
            _ = self._disconnect_task.cancel()
            self._disconnect_task = None

        # Register any new events with the Socket.IO client
        self._register_events(events)

        if resource_id not in self._listeners:
            self._listeners[resource_id] = []
            # Past the cap the server would reject the id, so it is not sent and stays unserved
            # until its last listener goes: its waits fall back to polling.
            if len(self._served) < MAX_SUBSCRIPTIONS_PER_CONNECTION:
                self._serve_seq += 1
                self._served[resource_id] = self._serve_seq
                self._emit("subscribe", resource_id)
        self._listeners[resource_id].append(handler)

        def unsubscribe() -> None:
            handlers = self._listeners.get(resource_id)
            if handlers and handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    self._unsubscribe_resource(resource_id)

        return unsubscribe

    def _register_events(self, events: list[str]) -> None:
        """Register Socket.IO event handlers (idempotent — each event is registered once)."""
        dispatcher = self

        for event_name in events:
            if event_name in self._registered_events:
                continue
            self._registered_events.add(event_name)

            # If socket isn't created yet, the event will be registered when connect() runs
            if not self._sio:
                continue

            def _make_handler(evt: str) -> Callable[..., Any]:
                async def _handler(data: Any) -> None:
                    resource_id = _extract_id_from_event(data)
                    if resource_id:
                        await dispatcher._dispatch(resource_id, evt, data)

                return _handler

            self._sio.on(event_name, _make_handler(event_name))  # pyright: ignore[reportUnusedCallResult]

    def _schedule_delayed_disconnect(self) -> None:
        generation = self._disconnect_generation

        async def _delayed() -> None:
            await asyncio.sleep(self._DISCONNECT_DELAY)
            if generation != self._disconnect_generation or self._listeners:
                return
            await self._disconnect(expected_generation=generation)

        try:
            self._disconnect_task = asyncio.get_running_loop().create_task(_delayed())
        except RuntimeError:
            pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def connection_epoch(self) -> int:
        """Counts successful connects (the first one and every reconnect)."""
        return self._connection_epoch

    def is_served(self, resource_id: str) -> bool:
        """Whether the server holds, or will hold after the next connect, a subscription for the id."""
        return resource_id in self._served

    def is_rejected(self, resource_id: str) -> bool:
        """Whether the server rejected the id's subscribe; it stays unserved until its last listener goes."""
        return resource_id in self._rejected

    def is_event_backed(self, resource_id: str) -> bool:
        """Whether state events for the id arrive now: connected, and the server holds the id."""
        return self._connected and resource_id in self._served

    @property
    def is_failed(self) -> bool:
        return self._failed

    @property
    def fail_error(self) -> str | None:
        return self._fail_error

    async def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        self._closed = True
        await self._disconnect()

    async def _disconnect(self, expected_generation: int | None = None) -> None:
        current = asyncio.current_task()
        cancelled: list[asyncio.Task[None]] = []

        if self._connect_task and not self._connect_task.done() and self._connect_task is not current:
            _ = self._connect_task.cancel()
            cancelled.append(self._connect_task)

        if self._disconnect_task and not self._disconnect_task.done():
            if self._disconnect_task is not current:
                _ = self._disconnect_task.cancel()
                cancelled.append(self._disconnect_task)
            self._disconnect_task = None

        async with self._lock:
            if expected_generation is not None:
                if expected_generation != self._disconnect_generation or self._listeners:
                    return

            sio = self._sio
            self._sio = None
            self._listeners.clear()
            self._served.clear()
            self._rejected.clear()
            self._pending_acks.clear()
            self._registered_events.clear()
            self._connected = False

        if sio:
            # aiohttp's ws.close() releases the websocket's connection and starts closing its
            # transport without waiting, so sio.disconnect() returns with the socket still open.
            ws_closed = _websocket_closed(sio)
            await sio.disconnect()
            if ws_closed is not None:
                _ = await asyncio.wait([ws_closed], timeout=self._CLOSE_TIMEOUT)
                if ws_closed.done() and not ws_closed.cancelled():
                    _ = ws_closed.exception()  # mark retrieved: a connection lost with an error is closed all the same

        # cancel() only requests cancellation, so wait for the cancelled tasks to finish: a loop stopped right
        # after close() (e.g. by run_until_complete) would leave them pending. Only now, after sio.disconnect():
        # a connect task cancelled mid-upgrade releases its connection from engine.io's aiohttp session as it
        # unwinds, after which closing that session no longer waits for the socket.
        if cancelled:
            _ = await asyncio.wait(cancelled, timeout=self._CLOSE_TIMEOUT)

    async def _dispatch(self, resource_id: str, event_name: str, data: Any) -> None:
        for handler in list(self._listeners.get(resource_id, [])):
            try:
                result = handler(event_name, data)
                if isinstance(result, Awaitable):
                    await result
            except Exception:
                logger.debug("Handler error for %s/%s", resource_id, event_name, exc_info=True)

    def _emit(self, event: str, resource_id: str) -> None:
        """Fire-and-forget subscribe/unsubscribe for one id while connected.

        While disconnected nothing is sent: the connect handler sends the full set.
        """
        sio = self._sio
        if sio is None or not self._connected:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # The token is taken now: by the time the send runs, the id may have been released and served again.
        token = self._served.get(resource_id, 0)
        task = loop.create_task(self._emit_serialized(sio, self._connection_epoch, event, resource_id, token))
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)

    async def _emit_serialized(
        self, sio: socketio.AsyncClient, epoch: int, event: str, resource_id: str, token: int
    ) -> None:
        async with self._emit_lock:
            # A reconnect in between already sent the current set; this message would be stale.
            if epoch != self._connection_epoch:
                return
            if event == "subscribe":
                await self._send_subscribe(sio, {resource_id: token})
            else:
                await _emit_async(sio, event, [resource_id])

    async def _send_subscribe(self, sio: socketio.AsyncClient, tokens: dict[str, int]) -> None:
        """Sends a subscribe for the ids (with their served tokens) that asks for an acknowledgement.

        Caller holds the emit lock.
        """
        epoch = self._connection_epoch

        async def on_ack(*args: Any) -> None:
            await self._handle_subscribe_ack(epoch, tokens, args)

        _prune_pending_acks(sio, self._pending_acks)
        self._pending_acks.append((time.monotonic() + _ACK_TIMEOUT, on_ack))
        await _emit_async(sio, "subscribe", list(tokens), on_ack)

    async def _handle_subscribe_ack(self, epoch: int, tokens: dict[str, int], args: tuple[Any, ...]) -> None:
        ack = _rejection(args)
        if ack is None or epoch != self._connection_epoch:
            return
        rejected = [i for i, token in tokens.items() if i in self._listeners and self._served.get(i) == token]
        for resource_id in rejected:
            # The slot is free again; no promotion: the id stays unserved until its last listener goes.
            del self._served[resource_id]
            self._rejected.add(resource_id)
        if rejected:
            logger.debug("Subscribe rejected for %d resource(s): %s", len(rejected), ack.get("error"))
        detail = {"error": ack.get("error"), "message": ack.get("message")}
        for resource_id in rejected:
            await self._dispatch(resource_id, SUBSCRIPTION_REJECTED_EVENT, detail)

    def _unsubscribe_resource(self, resource_id: str) -> None:
        if resource_id in self._listeners:
            del self._listeners[resource_id]
            if resource_id in self._served:
                del self._served[resource_id]
                self._emit("unsubscribe", resource_id)
            else:
                self._rejected.discard(resource_id)

        if not self._listeners:
            self._schedule_delayed_disconnect()


class SyncEventDispatcher:
    """Sync event dispatcher using socketio.Client on a background thread.

    Connects lazily and uses explicit subscriptions, like AsyncEventDispatcher.
    """

    _api_url: str
    _token: str
    _organization_id: str | None
    _sio: socketio.Client | None
    _connected: bool
    _failed: bool
    _fail_error: str | None
    _listeners: dict[str, list[EventHandler]]
    _registered_events: set[str]
    _lock: threading.Lock
    _bg_thread: threading.Thread | None
    _disconnect_timer: threading.Timer | None
    _disconnect_generation: int
    _closed: bool

    _DISCONNECT_DELAY: float = 30.0

    def __init__(
        self,
        api_url: str,
        token: str,
        organization_id: str | None = None,
        source: str | None = None,
        sdk_version: str | None = None,
    ):
        self._api_url = api_url
        self._token = token
        self._organization_id = organization_id
        self._source: str | None = source
        self._sdk_version: str | None = sdk_version
        self._sio = None
        self._connected = False
        self._failed = False
        self._fail_error = None
        self._listeners = {}
        # See AsyncEventDispatcher; all four are guarded by self._lock.
        self._served: dict[str, int] = {}
        self._serve_seq: int = 0
        self._rejected: set[str] = set()
        self._pending_acks: collections.deque[tuple[float, AckCallback]] = collections.deque()
        self._registered_events = set()
        self._lock = threading.Lock()
        # Bumped by the connect handler on the socket's thread; read by waiting threads.
        self._connection_epoch: int = 0
        self._epoch_lock: threading.Lock = threading.Lock()
        self._bg_thread = None
        self._disconnect_timer = None
        self._disconnect_generation = 0
        self._closed = False

    def ensure_connected(self) -> None:
        """Idempotent: ensure a connection attempt is in progress or already established.

        Non-blocking. Starts a background thread if not already connected and no
        attempt is currently running.
        """
        with self._lock:
            if self._closed or self._connected:
                return
            if self._bg_thread is not None and self._bg_thread.is_alive():
                return

            def _connect() -> None:
                try:
                    self.connect()
                except Exception:
                    pass  # Callers check is_connected when they need it

            self._bg_thread = threading.Thread(target=_connect, daemon=True)
            self._bg_thread.start()

    def connect(self, timeout: float = 5.0) -> None:
        """Establish the Socket.IO connection. Raises on failure."""
        with self._lock:
            if self._closed or self._connected:
                return
            old_sio = self._sio
            self._sio = None

        if old_sio:
            old_sio.disconnect()

        parsed = urlparse(self._api_url.rstrip("/"))
        origin = f"{parsed.scheme}://{parsed.netloc}"
        socketio_path = f"{parsed.path.rstrip('/')}/socket.io/"

        sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=1,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )
        with self._lock:
            self._sio = sio

        error_holder: list[str] = []
        dispatcher = self

        @sio.event  # type: ignore[misc]
        def connect() -> None:  # pyright: ignore[reportUnusedFunction]
            # Subscriptions belong to the socket: send the whole set on every connect and reconnect.
            # Emitting under the lock keeps these messages ordered with subscribe()/unsubscribe().
            with dispatcher._lock:
                with dispatcher._epoch_lock:
                    dispatcher._connection_epoch += 1
                dispatcher._connected = True
                dispatcher._failed = False
                dispatcher._fail_error = None
                # The previous socket's pending acknowledgements were dropped with it.
                dispatcher._pending_acks.clear()
                for chunk in _chunks(list(dispatcher._served)):
                    dispatcher._send_subscribe_locked(sio, {i: dispatcher._served[i] for i in chunk})

        @sio.event  # type: ignore[misc]
        def disconnect() -> None:  # pyright: ignore[reportUnusedFunction]
            with dispatcher._lock:
                dispatcher._connected = False

        @sio.event  # type: ignore[misc]
        def connect_error(data: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            dispatcher._connected = False
            dispatcher._failed = True
            dispatcher._fail_error = f"WebSocket connection failed: {data}"
            error_holder.append(str(data))

        try:
            # Re-register any events that were added before the socket was created
            with self._lock:
                pending_events = list(self._registered_events)
                self._registered_events.clear()
            self._register_events(pending_events)

            params: list[str] = []
            if self._organization_id:
                params.append(f"organizationId={quote(self._organization_id, safe='')}")
            if self._source:
                params.append(f"source={quote(self._source, safe='')}")
            if self._sdk_version:
                params.append(f"sdkVersion={quote(self._sdk_version, safe='')}")
            params.append("subscriptionMode=explicit")
            connect_url = f"{origin}?{'&'.join(params)}"

            sio.connect(
                connect_url,
                socketio_path=socketio_path,
                auth={"token": self._token},
                transports=["websocket"],
                headers={},
                wait=True,
                wait_timeout=max(1, int(timeout)),
            )

            if self._closed:
                self._sio = None
                self._connected = False
                sio.disconnect()
                return
        except Exception as e:
            self._failed = True
            self._fail_error = f"WebSocket connection failed: {e}"
            raise ConnectionError(self._fail_error) from e

        if not self._connected:
            self._failed = True
            err = error_holder[0] if error_holder else "unknown error"
            self._fail_error = f"WebSocket connection failed: {err}"
            raise ConnectionError(self._fail_error)

        with self._lock:
            if not self._listeners:
                generation = self._disconnect_generation
                self._disconnect_timer = threading.Timer(
                    self._DISCONNECT_DELAY,
                    self._delayed_disconnect,
                    args=(generation,),
                )
                self._disconnect_timer.daemon = True
                self._disconnect_timer.start()

    def subscribe(
        self,
        resource_id: str,
        handler: EventHandler,
        events: list[str],
    ) -> Callable[[], None]:
        """Subscribe to specific events for a resource.

        Args:
            resource_id: The ID of the resource (e.g. sandbox ID).
            handler: Callback receiving (event_name, raw_data).
            events: List of Socket.IO event names to listen for.

        Returns:
            Unsubscribe function.
        """
        with self._lock:
            if self._closed:
                return lambda: None
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
                self._disconnect_timer = None
            self._disconnect_generation += 1
            if resource_id not in self._listeners:
                self._listeners[resource_id] = []
                # Past the cap the server would reject the id, so it is not sent and stays unserved
                # until its last listener goes: its waits fall back to polling.
                if len(self._served) < MAX_SUBSCRIPTIONS_PER_CONNECTION:
                    self._serve_seq += 1
                    self._served[resource_id] = self._serve_seq
                    self._emit_locked("subscribe", resource_id)
            self._listeners[resource_id].append(handler)

        self.ensure_connected()
        self._register_events(events)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._listeners.get(resource_id)
                if handlers and handler in handlers:
                    handlers.remove(handler)
                    if not handlers:
                        self._unsubscribe_resource_locked(resource_id)
                if not self._listeners:
                    generation = self._disconnect_generation
                    self._disconnect_timer = threading.Timer(
                        self._DISCONNECT_DELAY,
                        self._delayed_disconnect,
                        args=(generation,),
                    )
                    self._disconnect_timer.daemon = True
                    self._disconnect_timer.start()

        return unsubscribe

    def _register_events(self, events: list[str]) -> None:
        """Register Socket.IO event handlers (idempotent — each event is registered once)."""
        dispatcher = self

        with self._lock:
            for event_name in events:
                if event_name in self._registered_events:
                    continue
                self._registered_events.add(event_name)

                # If socket isn't created yet, the event will be registered when connect() runs
                if not self._sio:
                    continue

                def _make_handler(evt: str) -> Callable[..., Any]:
                    def _handler(data: Any) -> None:
                        resource_id = _extract_id_from_event(data)
                        if resource_id:
                            dispatcher._dispatch(resource_id, evt, data)

                    return _handler

                self._sio.on(event_name, _make_handler(event_name))  # pyright: ignore[reportUnusedCallResult]

    def _delayed_disconnect(self, generation: int) -> None:
        self._disconnect(permanent=False, expected_generation=generation)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def connection_epoch(self) -> int:
        """Counts successful connects (the first one and every reconnect)."""
        with self._epoch_lock:
            return self._connection_epoch

    def is_served(self, resource_id: str) -> bool:
        """Whether the server holds, or will hold after the next connect, a subscription for the id."""
        with self._lock:
            return resource_id in self._served

    def is_rejected(self, resource_id: str) -> bool:
        """Whether the server rejected the id's subscribe; it stays unserved until its last listener goes."""
        with self._lock:
            return resource_id in self._rejected

    def is_event_backed(self, resource_id: str) -> bool:
        """Whether state events for the id arrive now: connected, and the server holds the id.

        Both are read under one lock, so a disconnect cannot land between the two reads.
        """
        with self._lock:
            return self._connected and resource_id in self._served

    @property
    def is_failed(self) -> bool:
        return self._failed

    @property
    def fail_error(self) -> str | None:
        return self._fail_error

    def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        self._disconnect(permanent=True)

    def _disconnect(self, permanent: bool, expected_generation: int | None = None) -> None:
        sio = None

        with self._lock:
            if expected_generation is not None:
                if expected_generation != self._disconnect_generation or self._listeners:
                    return
            if permanent:
                self._closed = True
            if self._disconnect_timer is not None:
                self._disconnect_timer.cancel()
                self._disconnect_timer = None
            sio = self._sio
            self._sio = None
            self._connected = False
            self._listeners.clear()
            self._served.clear()
            self._rejected.clear()
            self._pending_acks.clear()
            self._registered_events.clear()

        if sio:
            sio.disconnect()

    def _dispatch(self, resource_id: str, event_name: str, data: Any) -> None:
        with self._lock:
            handlers = list(self._listeners.get(resource_id, []))
        for handler in handlers:
            try:
                handler(event_name, data)
            except Exception:
                logger.debug("Handler error for %s/%s", resource_id, event_name, exc_info=True)

    def _unsubscribe_resource_locked(self, resource_id: str) -> None:
        if self._listeners.pop(resource_id, None) is None:
            return
        if resource_id in self._served:
            del self._served[resource_id]
            self._emit_locked("unsubscribe", resource_id)
        else:
            self._rejected.discard(resource_id)

    def _emit_locked(self, event: str, resource_id: str) -> None:
        """Fire-and-forget subscribe/unsubscribe for one id while connected. Caller holds self._lock.

        While disconnected nothing is sent: the connect handler sends the full set.
        """
        if self._sio is not None and self._connected:
            if event == "subscribe":
                self._send_subscribe_locked(self._sio, {resource_id: self._served[resource_id]})
            else:
                _emit_sync(self._sio, event, [resource_id])

    def _send_subscribe_locked(self, sio: socketio.Client, tokens: dict[str, int]) -> None:
        """Sends a subscribe for the ids (with their served tokens) that asks for an acknowledgement.

        Caller holds self._lock.
        """
        epoch = self._connection_epoch

        def on_ack(*args: Any) -> None:
            self._handle_subscribe_ack(epoch, tokens, args)

        _prune_pending_acks(sio, self._pending_acks)
        self._pending_acks.append((time.monotonic() + _ACK_TIMEOUT, on_ack))
        _emit_sync(sio, "subscribe", list(tokens), on_ack)

    def _handle_subscribe_ack(self, epoch: int, tokens: dict[str, int], args: tuple[Any, ...]) -> None:
        """Runs on the socket's thread."""
        ack = _rejection(args)
        if ack is None:
            return
        with self._lock:
            if epoch != self._connection_epoch:
                return
            rejected = [i for i, token in tokens.items() if i in self._listeners and self._served.get(i) == token]
            for resource_id in rejected:
                # The slot is free again; no promotion: the id stays unserved until its last listener goes.
                del self._served[resource_id]
                self._rejected.add(resource_id)
        if rejected:
            logger.debug("Subscribe rejected for %d resource(s): %s", len(rejected), ack.get("error"))
        detail = {"error": ack.get("error"), "message": ack.get("message")}
        for resource_id in rejected:
            self._dispatch(resource_id, SUBSCRIPTION_REJECTED_EVENT, detail)


def _chunks(resource_ids: list[str]) -> list[list[str]]:
    return [resource_ids[i : i + _SUBSCRIBE_CHUNK_SIZE] for i in range(0, len(resource_ids), _SUBSCRIBE_CHUNK_SIZE)]


async def _emit_async(
    sio: socketio.AsyncClient, event: str, resource_ids: list[str], callback: AckCallback | None = None
) -> None:
    try:
        await sio.emit(event, {"sandboxIds": resource_ids}, callback=callback)
    except Exception:
        logger.debug("Failed to send %s for %d resource(s)", event, len(resource_ids), exc_info=True)


def _emit_sync(sio: socketio.Client, event: str, resource_ids: list[str], callback: AckCallback | None = None) -> None:
    try:
        sio.emit(event, {"sandboxIds": resource_ids}, callback=callback)
    except Exception:
        logger.debug("Failed to send %s for %d resource(s)", event, len(resource_ids), exc_info=True)


def _rejection(args: tuple[Any, ...]) -> dict[str, Any] | None:
    """The acknowledgement payload if it rejects the subscribe (``{ok: false, error, message}``), else None."""
    ack: object = args[0] if args else None
    if not isinstance(ack, dict):
        return None
    payload = cast(dict[str, Any], ack)
    return payload if payload.get("ok") is False else None


def _prune_pending_acks(
    sio: socketio.Client | socketio.AsyncClient, pending: collections.deque[tuple[float, AckCallback]]
) -> None:
    """Drops acknowledgements unanswered for _ACK_TIMEOUT from python-socketio's pending table.

    Without this, a server that never acknowledges would keep one callback per subscribe for the
    connection's lifetime. Reads python-socketio internals (``callbacks["/"]``).
    """
    now = time.monotonic()
    expired: set[int] = set()
    while pending and pending[0][0] <= now:
        expired.add(id(pending.popleft()[1]))
    if not expired:
        return
    callbacks: object = getattr(sio, "callbacks", None)
    if not isinstance(callbacks, dict):
        return
    table: object = cast(dict[str, object], callbacks).get("/")
    if not isinstance(table, dict):
        return
    by_id = cast(dict[object, object], table)
    # One pass over one snapshot: linear in the table, however many acknowledgements expired at once.
    # The expired callbacks are still referenced by the table until popped, so their ids are not reused.
    for ack_id in [ack_id for ack_id, callback in by_id.items() if id(callback) in expired]:
        _ = by_id.pop(ack_id, None)


def _websocket_closed(sio: socketio.AsyncClient) -> asyncio.Future[None] | None:
    """Future resolved by aiohttp's ``connection_lost`` for the websocket's connection, if one is open.

    Reads python-engineio (``eio.ws``) and aiohttp (``ClientWebSocketResponse._conn``) internals, so call it
    before disconnecting: closing the websocket detaches its connection.
    """
    ws = getattr(getattr(sio, "eio", None), "ws", None)
    protocol = getattr(getattr(ws, "_conn", None), "protocol", None)
    closed = getattr(protocol, "closed", None)
    return closed if asyncio.isfuture(closed) else None


def _extract_id_from_event(data: Any) -> str | None:
    """Extract resource ID from an event payload.

    Handles two payload shapes:
      - Wrapper: {sandbox: {id: ...}, ...} → nested resource ID
      - Direct: {id: ...} → top-level ID
    """
    if not isinstance(data, dict):
        return None
    for key in ("sandbox", "volume", "snapshot", "runner"):
        nested: object = data.get(key)  # pyright: ignore[reportUnknownVariableType]
        if isinstance(nested, dict):
            sid: object = nested.get("id")  # pyright: ignore[reportUnknownVariableType]
            if isinstance(sid, str):
                return sid
    top_id: object = data.get("id")  # pyright: ignore[reportUnknownVariableType]
    if isinstance(top_id, str):
        return top_id
    return None
