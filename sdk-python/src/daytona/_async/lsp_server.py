# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from deprecated import deprecated

from daytona_toolbox_api_client_async import (
    CompletionList,
    LspApi,
    LspCompletionParams,
    LspDocumentRequest,
    LspPosition,
    LspServerRequest,
    LspSymbol,
)

from .._utils.errors import intercept_errors
from .._utils.otel_decorator import with_instrumentation
from .._utils.timeout import http_timeout
from ..common.lsp_server import LspCompletionPosition, LspLanguageId, LspLanguageIdLiteral


class AsyncLspServer:
    """Provides Language Server Protocol functionality for code intelligence to provide
    IDE-like features such as code completion, symbol search, and more.
    """

    def __init__(
        self,
        language_id: LspLanguageId | LspLanguageIdLiteral,
        path_to_project: str,
        api_client: LspApi,
    ):
        """Initializes a new LSP server instance.

        Args:
            language_id (LspLanguageId | LspLanguageIdLiteral): The language server type
                (e.g., LspLanguageId.TYPESCRIPT).
            path_to_project (str): Absolute path to the project root directory.
            api_client (LspApi): API client for Sandbox operations.
        """
        self._language_id: str = str(language_id)
        self._path_to_project: str = path_to_project
        self._api_client: LspApi = api_client

    @intercept_errors(message_prefix="Failed to start LSP server: ")
    @with_instrumentation()
    async def start(self, request_timeout: float | None = None) -> None:
        """Starts the language server.

        This method must be called before using any other LSP functionality.
        It initializes the language server for the specified language and project.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            lsp = sandbox.create_lsp_server("typescript", "workspace/project")
            await lsp.start()  # Initialize the server
            # Now ready for LSP operations
            ```
        """
        await self._api_client.start(
            request=LspServerRequest(
                language_id=self._language_id,
                path_to_project=self._path_to_project,
            ),
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to stop LSP server: ")
    @with_instrumentation()
    async def stop(self, request_timeout: float | None = None) -> None:
        """Stops the language server.

        This method should be called when the LSP server is no longer needed to
        free up system resources.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            # When done with LSP features
            await lsp.stop()  # Clean up resources
            ```
        """
        await self._api_client.stop(
            request=LspServerRequest(
                language_id=self._language_id,
                path_to_project=self._path_to_project,
            ),
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to open file: ")
    @with_instrumentation()
    async def did_open(self, path: str, request_timeout: float | None = None) -> None:
        """Notifies the language server that a file has been opened.

        This method should be called when a file is opened in the editor to enable
        language features like diagnostics and completions for that file. The server
        will begin tracking the file's contents and providing language features.

        Args:
            path (str): Path to the opened file. Relative paths are resolved based on the project path
            set in the LSP server constructor.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            # When opening a file for editing
            await lsp.did_open("workspace/project/src/index.ts")
            # Now can get completions, symbols, etc. for this file
            ```
        """
        await self._api_client.did_open(
            request=LspDocumentRequest(
                language_id=self._language_id,
                path_to_project=self._path_to_project,
                uri=f"file://{path}",
            ),
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to close file: ")
    @with_instrumentation()
    async def did_close(self, path: str, request_timeout: float | None = None) -> None:
        """Notify the language server that a file has been closed.

        This method should be called when a file is closed in the editor to allow
        the language server to clean up any resources associated with that file.

        Args:
            path (str): Path to the closed file. Relative paths are resolved based on the project path
            set in the LSP server constructor.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            # When done editing a file
            await lsp.did_close("workspace/project/src/index.ts")
            ```
        """
        await self._api_client.did_close(
            request=LspDocumentRequest(
                language_id=self._language_id,
                path_to_project=self._path_to_project,
                uri=f"file://{path}",
            ),
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to get symbols from document: ")
    @with_instrumentation()
    async def document_symbols(self, path: str, request_timeout: float | None = None) -> list[LspSymbol]:
        """Gets symbol information (functions, classes, variables, etc.) from a document.

        Args:
            path (str): Path to the file to get symbols from. Relative paths are resolved based on the project path
            set in the LSP server constructor.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            list[LspSymbol]: List of symbols in the document. Each symbol includes:
                - name: The symbol's name
                - kind: The symbol's kind (function, class, variable, etc.)
                - location: The location of the symbol in the file

        Example:
            ```python
            # Get all symbols in a file
            symbols = await lsp.document_symbols("workspace/project/src/index.ts")
            for symbol in symbols:
                print(f"{symbol.kind} {symbol.name}: {symbol.location}")
            ```
        """
        return await self._api_client.document_symbols(
            language_id=self._language_id,
            path_to_project=self._path_to_project,
            uri=f"file://{path}",
            _request_timeout=http_timeout(request_timeout),
        )

    @deprecated(
        reason="Method is deprecated. Use `sandbox_symbols` instead. This method will be removed in a future version."
    )
    @with_instrumentation()
    async def workspace_symbols(self, query: str, request_timeout: float | None = None) -> list[LspSymbol]:
        """Searches for symbols matching the query string across all files
        in the Sandbox.

        Args:
            query (str): Search query to match against symbol names.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            list[LspSymbol]: List of matching symbols from all files.
        """
        return await self.sandbox_symbols(query, request_timeout=request_timeout)

    @intercept_errors(message_prefix="Failed to get symbols from sandbox: ")
    @with_instrumentation()
    async def sandbox_symbols(self, query: str, request_timeout: float | None = None) -> list[LspSymbol]:
        """Searches for symbols matching the query string across all files
        in the Sandbox.

        Args:
            query (str): Search query to match against symbol names.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            list[LspSymbol]: List of matching symbols from all files. Each symbol
                includes:
                - name: The symbol's name
                - kind: The symbol's kind (function, class, variable, etc.)
                - location: The location of the symbol in the file

        Example:
            ```python
            # Search for all symbols containing "User"
            symbols = await lsp.sandbox_symbols("User")
            for symbol in symbols:
                print(f"{symbol.name} in {symbol.location}")
            ```
        """
        return await self._api_client.workspace_symbols(
            language_id=self._language_id,
            path_to_project=self._path_to_project,
            query=query,
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to get completions: ")
    @with_instrumentation()
    async def completions(
        self, path: str, position: LspCompletionPosition, request_timeout: float | None = None
    ) -> CompletionList:
        """Gets completion suggestions at a position in a file.

        Args:
            path (str): Path to the file. Relative paths are resolved based on the project path
            set in the LSP server constructor.
            position (LspCompletionPosition): Cursor position to get completions for.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            CompletionList: List of completion suggestions. The list includes:
                - isIncomplete: Whether more items might be available
                - items: List of completion items, each containing:
                    - label: The text to insert
                    - kind: The kind of completion
                    - detail: Additional details about the item
                    - documentation: Documentation for the item
                    - sortText: Text used to sort the item in the list
                    - filterText: Text used to filter the item
                    - insertText: The actual text to insert (if different from label)

        Example:
            ```python
            # Get completions at a specific position
            pos = LspCompletionPosition(line=10, character=15)
            completions = await lsp.completions("workspace/project/src/index.ts", pos)
            for item in completions.items:
                print(f"{item.label} ({item.kind}): {item.detail}")
            ```
        """
        return await self._api_client.completions(
            request=LspCompletionParams(
                language_id=self._language_id,
                path_to_project=self._path_to_project,
                uri=f"file://{path}",
                position=LspPosition(line=position.line, character=position.character),
            ),
            _request_timeout=http_timeout(request_timeout),
        )
