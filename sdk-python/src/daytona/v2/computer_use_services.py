# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Optional, Sequence, overload

from ._body_limit import validated_max_response_bytes
from ._core import RequestSpec, path_segment, timeout_millis_from_seconds
from ._transport import AsyncTransport
from .computer_use_core import (
    a11y_max_depth,
    computer_use_params,
    expect_computer_use_display_info_response,
    expect_computer_use_display_windows_response,
    expect_computer_use_point,
    expect_computer_use_process_errors_response,
    expect_computer_use_process_logs_response,
    expect_computer_use_process_restart_response,
    expect_computer_use_process_running_status,
    expect_computer_use_process_status_map,
    expect_computer_use_recording,
    expect_computer_use_recordings_response,
    expect_computer_use_screenshot_response,
    expect_computer_use_scroll_response,
    parse_computer_use_a11y_find_payload,
    parse_computer_use_a11y_tree_payload,
    parse_computer_use_displays,
    parse_computer_use_point,
    parse_computer_use_process_errors,
    parse_computer_use_process_logs,
    parse_computer_use_process_restart,
    parse_computer_use_process_running_status,
    parse_computer_use_process_status_map,
    parse_computer_use_recording,
    parse_computer_use_recording_list,
    parse_computer_use_screenshot,
    parse_computer_use_scroll,
    parse_computer_use_windows,
)
from .computer_use_types import (
    ComputerUseA11yFindResult,
    ComputerUseA11yNameMatch,
    ComputerUseA11yScope,
    ComputerUseA11yTree,
    ComputerUseDisplayInfo,
    ComputerUseDisplayWindows,
    ComputerUseKeyboardModifier,
    ComputerUseMouseButton,
    ComputerUsePoint,
    ComputerUseProcessErrors,
    ComputerUseProcessLogs,
    ComputerUseProcessRestartResult,
    ComputerUseProcessRunningStatus,
    ComputerUseProcessStatus,
    ComputerUseRecording,
    ComputerUseRecordingList,
    ComputerUseRegion,
    ComputerUseScreenshot,
    ComputerUseScreenshotFormat,
    ComputerUseScrollDirection,
    ComputerUseScrollResult,
)
from .types import JsonValue


def _quote_path_segment(value: str, name: str) -> str:
    return path_segment(value, name)


class AsyncAddonProcessesService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    @overload
    async def status(self) -> dict[str, ComputerUseProcessStatus]: ...

    @overload
    async def status(self, name: str, /) -> ComputerUseProcessRunningStatus: ...

    async def status(
        self,
        name: str | None = None,
    ) -> dict[str, ComputerUseProcessStatus] | ComputerUseProcessRunningStatus:
        if name is None:
            return parse_computer_use_process_status_map(
                expect_computer_use_process_status_map(
                    await self._transport.send(RequestSpec("GET", "/computeruse/process-status"))
                )
            )
        return parse_computer_use_process_running_status(
            expect_computer_use_process_running_status(
                await self._transport.send(
                    RequestSpec(
                        "GET",
                        f"/computeruse/process/{_quote_path_segment(name, 'process name')}/status",
                    )
                )
            )
        )

    async def restart(self, name: str, /) -> ComputerUseProcessRestartResult:
        return parse_computer_use_process_restart(
            expect_computer_use_process_restart_response(
                await self._transport.send(
                    RequestSpec(
                        "POST",
                        f"/computeruse/process/{_quote_path_segment(name, 'process name')}/restart",
                    )
                )
            )
        )

    async def logs(self, name: str, /) -> ComputerUseProcessLogs:
        return parse_computer_use_process_logs(
            expect_computer_use_process_logs_response(
                await self._transport.send(
                    RequestSpec(
                        "GET",
                        f"/computeruse/process/{_quote_path_segment(name, 'process name')}/logs",
                    )
                )
            )
        )

    async def errors(self, name: str, /) -> ComputerUseProcessErrors:
        return parse_computer_use_process_errors(
            expect_computer_use_process_errors_response(
                await self._transport.send(
                    RequestSpec(
                        "GET",
                        f"/computeruse/process/{_quote_path_segment(name, 'process name')}/errors",
                    )
                )
            )
        )


class AsyncMouseService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def position(self) -> ComputerUsePoint:
        return parse_computer_use_point(
            expect_computer_use_point(
                await self._transport.send(RequestSpec("GET", "/computeruse/mouse/position"))
            )
        )

    async def move(self, *, x: int, y: int) -> ComputerUsePoint:
        return parse_computer_use_point(
            expect_computer_use_point(
                await self._transport.send(
                    RequestSpec("POST", "/computeruse/mouse/move", json_body={"x": x, "y": y})
                )
            )
        )

    async def click(
        self,
        *,
        x: int,
        y: int,
        button: ComputerUseMouseButton | None = None,
        double: bool | None = None,
    ) -> ComputerUsePoint:
        return parse_computer_use_point(
            expect_computer_use_point(
                await self._transport.send(
                    RequestSpec(
                        "POST",
                        "/computeruse/mouse/click",
                        json_body=computer_use_params(x=x, y=y, button=button, double=double),
                    )
                )
            )
        )

    async def drag(
        self,
        *,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: ComputerUseMouseButton | None = None,
    ) -> ComputerUsePoint:
        return parse_computer_use_point(
            expect_computer_use_point(
                await self._transport.send(
                    RequestSpec(
                        "POST",
                        "/computeruse/mouse/drag",
                        json_body=computer_use_params(
                            startX=start_x,
                            startY=start_y,
                            endX=end_x,
                            endY=end_y,
                            button=button,
                        ),
                    )
                )
            )
        )

    async def scroll(
        self,
        *,
        x: int,
        y: int,
        direction: ComputerUseScrollDirection,
        amount: int | None = None,
    ) -> ComputerUseScrollResult:
        return parse_computer_use_scroll(
            expect_computer_use_scroll_response(
                await self._transport.send(
                    RequestSpec(
                        "POST",
                        "/computeruse/mouse/scroll",
                        json_body=computer_use_params(x=x, y=y, direction=direction, amount=amount),
                    )
                )
            )
        )


class AsyncKeyboardService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def type(self, *, text: str, delay: float | None = None) -> None:
        """Type ``text``; ``delay`` is the pause between key presses in seconds."""
        await self._transport.send(
            RequestSpec(
                "POST",
                "/computeruse/keyboard/type",
                json_body=computer_use_params(text=text, delay=timeout_millis_from_seconds(delay, "delay")),
            )
        )

    async def key(
        self,
        *,
        key: str,
        modifiers: Sequence[ComputerUseKeyboardModifier] | None = None,
    ) -> None:
        body = computer_use_params(key=key)
        if modifiers is not None:
            body["modifiers"] = list(modifiers)
        await self._transport.send(RequestSpec("POST", "/computeruse/keyboard/key", json_body=body))

    async def hotkey(self, *, keys: str) -> None:
        await self._transport.send(
            RequestSpec("POST", "/computeruse/keyboard/hotkey", json_body={"keys": keys})
        )


class AsyncScreenService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def screenshot(
        self,
        *,
        region: ComputerUseRegion | None = None,
        format: ComputerUseScreenshotFormat | None = None,
        quality: int | None = None,
        scale: float | None = None,
        show_cursor: bool | None = None,
    ) -> ComputerUseScreenshot:
        """Capture the screen, or ``region`` of it.

        Setting any of ``format``, ``quality`` or ``scale`` returns a compressed image.
        """
        compressed = format is not None or quality is not None or scale is not None
        params: dict[str, JsonValue] = {}
        if region is not None:
            params.update(
                computer_use_params(x=region.x, y=region.y, width=region.width, height=region.height)
            )
        if compressed:
            params.update(computer_use_params(format=format, quality=quality, scale=scale))
        if show_cursor is not None:
            params["show_cursor"] = str(show_cursor).lower()
        region_route = (
            "/computeruse/screenshot/region/compressed" if compressed else "/computeruse/screenshot/region"
        )
        full_route = "/computeruse/screenshot/compressed" if compressed else "/computeruse/screenshot"
        route = full_route if region is None else region_route
        return parse_computer_use_screenshot(
            expect_computer_use_screenshot_response(
                await self._transport.send(RequestSpec("GET", route, params=params))
            )
        )


class AsyncDisplayService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def info(self) -> ComputerUseDisplayInfo:
        return ComputerUseDisplayInfo(
            displays=parse_computer_use_displays(
                expect_computer_use_display_info_response(
                    await self._transport.send(RequestSpec("GET", "/computeruse/display/info"))
                )
            )
        )

    async def windows(self) -> ComputerUseDisplayWindows:
        return ComputerUseDisplayWindows(
            windows=parse_computer_use_windows(
                expect_computer_use_display_windows_response(
                    await self._transport.send(RequestSpec("GET", "/computeruse/display/windows"))
                )
            )
        )


class AsyncA11yService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def tree(
        self,
        *,
        scope: ComputerUseA11yScope,
        pid: int | None = None,
        max_depth: int | None = None,
    ) -> ComputerUseA11yTree:
        """Read the accessibility tree, at most 256 levels below the root.

        ``max_depth`` is how many levels below the root to return: an integer
        from 0 to 256 (the SDK's cap), default 256. A deeper UI comes back cut
        at that depth with ``truncated`` set; ``-1`` (unbounded) is refused.
        """
        depth = a11y_max_depth(max_depth)
        return parse_computer_use_a11y_tree_payload(
            await self._transport.send(
                RequestSpec(
                    "GET",
                    "/computeruse/a11y/tree",
                    params=computer_use_params(scope=scope, pid=pid, maxDepth=depth),
                )
            ),
            max_depth=depth,
        )

    async def find(
        self,
        *,
        scope: ComputerUseA11yScope,
        pid: int | None = None,
        role: str | None = None,
        name: str | None = None,
        name_match: ComputerUseA11yNameMatch | None = None,
        states: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> ComputerUseA11yFindResult:
        body = computer_use_params(
            scope=scope,
            pid=pid,
            role=role,
            name=name,
            nameMatch=name_match,
            limit=limit,
        )
        if states is not None:
            body["states"] = list(states)
        return parse_computer_use_a11y_find_payload(
            await self._transport.send(RequestSpec("POST", "/computeruse/a11y/find", json_body=body))
        )

    async def focus(self, *, id: str) -> None:
        await self._transport.send(RequestSpec("POST", "/computeruse/a11y/node/focus", json_body={"id": id}))

    async def invoke(self, *, id: str, action: str | None = None) -> None:
        await self._transport.send(
            RequestSpec(
                "POST",
                "/computeruse/a11y/node/invoke",
                json_body=computer_use_params(id=id, action=action),
            )
        )

    async def set_value(self, *, id: str, value: str) -> None:
        await self._transport.send(
            RequestSpec("POST", "/computeruse/a11y/node/value", json_body={"id": id, "value": value})
        )


class AsyncRecordingService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def start(self, *, label: str | None = None) -> ComputerUseRecording:
        return parse_computer_use_recording(
            expect_computer_use_recording(
                await self._transport.send(
                    RequestSpec(
                        "POST",
                        "/computeruse/recordings/start",
                        json_body=computer_use_params(label=label),
                    )
                )
            )
        )

    async def stop(self, *, id: str) -> ComputerUseRecording:
        return parse_computer_use_recording(
            expect_computer_use_recording(
                await self._transport.send(
                    RequestSpec(
                        "POST",
                        "/computeruse/recordings/stop",
                        json_body={"id": id},
                    )
                )
            )
        )

    async def list(self) -> ComputerUseRecordingList:
        return parse_computer_use_recording_list(
            expect_computer_use_recordings_response(
                await self._transport.send(RequestSpec("GET", "/computeruse/recordings"))
            )
        )

    async def get(self, id: str, /) -> ComputerUseRecording:
        return parse_computer_use_recording(
            expect_computer_use_recording(
                await self._transport.send(
                    RequestSpec("GET", f"/computeruse/recordings/{_quote_path_segment(id, 'recording id')}")
                )
            )
        )

    async def download(self, id: str, /, *, max_response_bytes: Optional[int] = None) -> bytes:
        """Download a recording, holding the whole file in memory.

        No total deadline (it fails only when no bytes move for the client's
        ``request_timeout``; with a ``request_timeout`` of ``0``, nothing
        bounds it). ``max_response_bytes`` replaces the client's cap
        on the body for this call (``None`` keeps the client's cap, ``0`` means
        no limit, and any other value (negative, fractional, boolean, string,
        above 2**53 - 1) is a ``BadRequestError``); a larger recording raises
        ``PayloadTooLargeError`` before it is read in full. For a large
        recording, or one from a sandbox you do not trust, download its
        ``file_path`` with ``sandbox.fs.download_file()``, which streams to disk
        with progress and cancellation.
        """
        limit = (
            None
            if max_response_bytes is None
            else validated_max_response_bytes("max_response_bytes", max_response_bytes)
        )
        return await self._transport.raw(
            RequestSpec(
                "GET",
                f"/computeruse/recordings/{_quote_path_segment(id, 'recording id')}/download",
                transfer=True,
                max_response_bytes=limit,
            )
        )

    async def delete(self, id: str, /) -> None:
        await self._transport.send(
            RequestSpec("DELETE", f"/computeruse/recordings/{_quote_path_segment(id, 'recording id')}")
        )
