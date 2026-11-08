# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import List, Mapping, Optional, Union

from typing_extensions import Self

from ._core import RequestSpec, TransportPayload, _expect_model_from_dict
from ._gen.toolbox import (
    CompletionItem,
    CompletionList,
    MarkupContent,
    SymbolInfo,
    SymbolPosition,
)
from ._transport import AsyncTransport
from .errors import DaytonaError, create_error
from .types import JsonObject, JsonValue


@dataclass(frozen=True, kw_only=True)
class LspPosition:
    """A zero-based line / UTF-16 character position in a document."""

    line: int
    character: int


@dataclass(frozen=True, kw_only=True)
class LspRange:
    start: LspPosition
    end: LspPosition


@dataclass(frozen=True, kw_only=True)
class LspLocation:
    uri: str
    range: LspRange


@dataclass(frozen=True, kw_only=True)
class LspSymbol:
    """One symbol from ``document_symbols()`` / ``workspace_symbols()``."""

    name: str
    kind: int
    """The LSP ``SymbolKind`` number."""
    location: LspLocation


@dataclass(frozen=True, kw_only=True)
class LspMarkupContent:
    """Marked-up completion documentation."""

    kind: str
    """Typically ``"markdown"`` or ``"plaintext"``."""
    value: str


@dataclass(frozen=True, kw_only=True)
class LspCompletionItem:
    label: str
    kind: Optional[int] = None
    """The LSP ``CompletionItemKind`` number."""
    detail: Optional[str] = None
    documentation: Union[str, LspMarkupContent, None] = None
    sort_text: Optional[str] = None
    filter_text: Optional[str] = None
    insert_text: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class LspCompletionList:
    is_incomplete: bool
    items: List[LspCompletionItem]


@dataclass(frozen=True, kw_only=True)
class LspCompletionContext:
    """How a completion request was triggered."""

    trigger_kind: int
    """The LSP ``CompletionTriggerKind`` number."""
    trigger_character: Optional[str] = None


def _position(value: SymbolPosition) -> LspPosition:
    return LspPosition(line=value.line, character=value.character)


def _symbol(value: SymbolInfo) -> LspSymbol:
    location = value.location
    return LspSymbol(
        name=value.name,
        kind=value.kind,
        location=LspLocation(
            uri=location.uri,
            range=LspRange(start=_position(location.range.start), end=_position(location.range.end)),
        ),
    )


def _completion_item(value: CompletionItem) -> LspCompletionItem:
    documentation: Union[str, LspMarkupContent, None] = None
    if value.documentation is not None:
        actual = value.documentation.actual_instance
        if isinstance(actual, MarkupContent):
            documentation = LspMarkupContent(kind=actual.kind, value=actual.value)
        elif isinstance(actual, str):
            documentation = actual
    return LspCompletionItem(
        label=value.label,
        kind=value.kind,
        detail=value.detail,
        documentation=documentation,
        sort_text=value.sort_text,
        filter_text=value.filter_text,
        insert_text=value.insert_text,
    )


def _expect_completion_list(value: TransportPayload) -> LspCompletionList:
    try:
        model = _expect_model_from_dict(
            value, CompletionList, "LSP completion list", CompletionList.from_dict
        )
    except (KeyError, TypeError, ValueError) as exc:
        # Only the documentation union raises these (its generated oneOf parser).
        raise create_error(
            "expected LSP completion list: documentation must be a string or a markup content with kind and value",
            400,
            "BAD_REQUEST",
            "SDK",
        ) from exc
    return LspCompletionList(
        is_incomplete=model.is_incomplete, items=[_completion_item(item) for item in model.items]
    )


def _expect_symbol_list(value: TransportPayload) -> list[LspSymbol]:
    if not isinstance(value, list):
        raise create_error("expected LSP symbol list", 400, "BAD_REQUEST", "SDK")
    symbols: list[LspSymbol] = []
    for item in value:
        if not isinstance(item, dict):
            raise create_error("expected LSP symbol list", 400, "BAD_REQUEST", "SDK")
        try:
            symbols.append(
                _symbol(_expect_model_from_dict(item, SymbolInfo, "LSP symbol", SymbolInfo.from_dict))
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise create_error("expected LSP symbol list", 400, "BAD_REQUEST", "SDK") from exc
    return symbols


def _position_payload(position: LspPosition | Mapping[str, int]) -> JsonObject:
    if not isinstance(position, LspPosition):
        if not isinstance(position, Mapping):
            raise create_error("expected LSP position", 400, "BAD_REQUEST", "SDK")
        unknown = sorted(str(key) for key in position if key not in ("line", "character"))
        if unknown:
            raise create_error(
                f"LSP position: unknown key {unknown[0]!r} (expected line and character)",
                400,
                "BAD_REQUEST",
                "SDK",
            )
    raw: JsonObject = (
        {"line": position.line, "character": position.character}
        if isinstance(position, LspPosition)
        else dict(position)
    )
    model = _expect_model_from_dict(raw, SymbolPosition, "LSP position", SymbolPosition.from_dict)
    return {"line": model.line, "character": model.character}


def _context_payload(context: LspCompletionContext) -> JsonObject:
    if not isinstance(context, LspCompletionContext):
        raise create_error("expected LSP completion context", 400, "BAD_REQUEST", "SDK")
    if isinstance(context.trigger_kind, bool) or not isinstance(context.trigger_kind, int):
        raise create_error(
            "LSP completion context: trigger_kind must be an integer", 400, "BAD_REQUEST", "SDK"
        )
    if context.trigger_character is not None and not isinstance(context.trigger_character, str):
        raise create_error(
            "LSP completion context: trigger_character must be a string", 400, "BAD_REQUEST", "SDK"
        )
    payload: JsonObject = {"triggerKind": context.trigger_kind}
    if context.trigger_character is not None:
        payload["triggerCharacter"] = context.trigger_character
    return payload


class AsyncLspServer:
    """A started language server for one project, returned by ``lsp.start()``."""

    def __init__(self, _transport: AsyncTransport, _language_id: str, _path_to_project: str) -> None:
        self._transport = _transport
        self._language_id = _language_id
        self._path_to_project = _path_to_project
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
        context: Optional[LspCompletionContext] = None,
    ) -> LspCompletionList:
        body: dict[str, JsonValue] = {
            "languageId": self._language_id,
            "pathToProject": self._path_to_project,
            "uri": uri,
            "position": _position_payload(position),
        }
        if context is not None:
            body["context"] = _context_payload(context)
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
    """Sandbox LSP namespace: start language servers."""

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def start(self, language_id: str, path_to_project: str) -> AsyncLspServer:
        await self._transport.send(
            RequestSpec(
                "POST",
                "/lsp/start",
                json_body={"languageId": language_id, "pathToProject": path_to_project},
            )
        )
        return AsyncLspServer(self._transport, language_id, path_to_project)
