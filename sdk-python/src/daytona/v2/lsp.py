# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import TracebackType
from typing import Mapping, TypeAlias

from typing_extensions import Self

from ._core import RequestSpec, TransportPayload, _expect_model_from_dict
from ._gen.toolbox import (
    CompletionItem,
    CompletionList,
    SymbolInfo,
    SymbolLocation,
    SymbolPosition,
    SymbolRange,
)
from ._transport import AsyncTransport
from .errors import DaytonaError, create_error
from .types import JsonObject, JsonValue

LspPosition: TypeAlias = SymbolPosition
LspRange: TypeAlias = SymbolRange
LspLocation: TypeAlias = SymbolLocation
LspSymbol: TypeAlias = SymbolInfo
LspCompletionItem: TypeAlias = CompletionItem
LspCompletionList: TypeAlias = CompletionList


def _expect_completion_list(value: TransportPayload) -> LspCompletionList:
    try:
        return _expect_model_from_dict(value, CompletionList, "LSP completion list", CompletionList.from_dict)
    except (KeyError, TypeError, ValueError) as exc:
        raise create_error("expected LSP completion list", 400, "BAD_REQUEST", "SDK") from exc


def _expect_symbol_list(value: TransportPayload) -> list[LspSymbol]:
    if not isinstance(value, list):
        raise create_error("expected LSP symbol list", 400, "BAD_REQUEST", "SDK")
    symbols: list[LspSymbol] = []
    for item in value:
        if not isinstance(item, dict):
            raise create_error("expected LSP symbol list", 400, "BAD_REQUEST", "SDK")
        try:
            symbols.append(_expect_model_from_dict(item, SymbolInfo, "LSP symbol", SymbolInfo.from_dict))
        except (KeyError, TypeError, ValueError) as exc:
            raise create_error("expected LSP symbol list", 400, "BAD_REQUEST", "SDK") from exc
    return symbols


def _position_payload(position: LspPosition | Mapping[str, int]) -> JsonObject:
    if isinstance(position, SymbolPosition):
        return position.to_dict()
    return _expect_model_from_dict(
        dict(position), SymbolPosition, "LSP position", SymbolPosition.from_dict
    ).to_dict()


class AsyncLspServer:
    def __init__(self, transport: AsyncTransport, language_id: str, path_to_project: str) -> None:
        self._transport = transport
        self._language_id = language_id
        self._path_to_project = path_to_project
        self._stopped = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.stop()

    async def open_document(self, uri: str) -> None:
        """Notify the server that the document at ``uri`` was opened."""
        await self._transport.send(
            RequestSpec(
                "POST",
                "/lsp/did-open",
                json_body={
                    "languageId": self._language_id,
                    "pathToProject": self._path_to_project,
                    "uri": uri,
                },
            )
        )

    async def close_document(self, uri: str) -> None:
        """Notify the server that the document at ``uri`` was closed."""
        await self._transport.send(
            RequestSpec(
                "POST",
                "/lsp/did-close",
                json_body={
                    "languageId": self._language_id,
                    "pathToProject": self._path_to_project,
                    "uri": uri,
                },
            )
        )

    async def completions(
        self,
        uri: str,
        position: LspPosition | Mapping[str, int],
        *,
        context: JsonValue | None = None,
    ) -> LspCompletionList:
        body: dict[str, JsonValue] = {
            "languageId": self._language_id,
            "pathToProject": self._path_to_project,
            "uri": uri,
            "position": _position_payload(position),
        }
        if context is not None:
            body["context"] = context
        return _expect_completion_list(
            await self._transport.send(RequestSpec("POST", "/lsp/completions", json_body=body))
        )

    async def document_symbols(self, uri: str) -> list[LspSymbol]:
        payload = await self._transport.send(
            RequestSpec(
                "GET",
                "/lsp/document-symbols",
                params={
                    "languageId": self._language_id,
                    "pathToProject": self._path_to_project,
                    "uri": uri,
                },
            )
        )
        return _expect_symbol_list(payload)

    async def workspace_symbols(self, query: str) -> list[LspSymbol]:
        payload = await self._transport.send(
            RequestSpec(
                "GET",
                "/lsp/workspacesymbols",
                params={
                    "query": query,
                    "languageId": self._language_id,
                    "pathToProject": self._path_to_project,
                },
            )
        )
        return _expect_symbol_list(payload)

    async def stop(self) -> None:
        if self._stopped:
            return
        try:
            await self._transport.send(
                RequestSpec(
                    "POST",
                    "/lsp/stop",
                    json_body={"languageId": self._language_id, "pathToProject": self._path_to_project},
                )
            )
        except DaytonaError as exc:
            if exc.code != "LSP_NOT_STARTED":
                raise
        self._stopped = True


class AsyncLspService:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def start(self, language_id: str, path_to_project: str) -> AsyncLspServer:
        await self._transport.send(
            RequestSpec(
                "POST",
                "/lsp/start",
                json_body={"languageId": language_id, "pathToProject": path_to_project},
            )
        )
        return AsyncLspServer(self._transport, language_id, path_to_project)
