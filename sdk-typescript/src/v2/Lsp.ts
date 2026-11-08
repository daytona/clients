/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { encodeQuery, HttpTransport } from './http.js'
import { BadRequestError, DaytonaError } from './errors/DaytonaError.js'
import type * as toolbox from './gen/toolbox-types.js'

/** A zero-based LSP position. */
export type LspPosition = toolbox.SymbolPosition

export type LspRange = toolbox.SymbolRange

export type LspLocation = toolbox.SymbolLocation

/** One symbol from `documentSymbols()` / `workspaceSymbols()`. */
export type LspSymbol = toolbox.SymbolInfo

/** One completion candidate, with the shape supplied by the language server. */
export type LspCompletionItem = {
  [key: string]: unknown
} & Omit<toolbox.CompletionItem, 'documentation'> & {
  documentation?: unknown
}

export type LspCompletionList = Omit<toolbox.CompletionList, 'items'> & { items: LspCompletionItem[] }

export interface LspCompletionsOptions {
  readonly context?: toolbox.CompletionRequest['context']
}

function serverRequest(languageId: string, pathToProject: string): toolbox.ServerRequest {
  return { languageId, pathToProject }
}

function documentRequest(languageId: string, pathToProject: string, uri: string): toolbox.DocumentRequest {
  return { languageId, pathToProject, uri }
}

function positionPayload(position: LspPosition): toolbox.SymbolPosition {
  if (!Number.isInteger(position.line) || position.line < 0) {
    throw new BadRequestError('expected LSP position: line must be a non-negative integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  if (!Number.isInteger(position.character) || position.character < 0) {
    throw new BadRequestError('expected LSP position: character must be a non-negative integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return { line: position.line, character: position.character }
}

export class LspServer {
  private stopped = false

  constructor(
    private readonly transport: HttpTransport,
    private readonly languageId: string,
    private readonly pathToProject: string,
  ) {}

  /** Notify the server that the document at `uri` was opened. */
  public async openDocument(uri: string): Promise<void> {
    await this.transport.post<void>('/lsp/did-open', documentRequest(this.languageId, this.pathToProject, uri))
  }

  /** Notify the server that the document at `uri` was closed. */
  public async closeDocument(uri: string): Promise<void> {
    await this.transport.post<void>('/lsp/did-close', documentRequest(this.languageId, this.pathToProject, uri))
  }

  public async completions(uri: string, position: LspPosition, options: LspCompletionsOptions = {}): Promise<LspCompletionList> {
    const body: toolbox.CompletionRequest = {
      languageId: this.languageId,
      pathToProject: this.pathToProject,
      uri,
      position: positionPayload(position),
    }
    if (options.context !== undefined) {
      body.context = options.context
    }
    return this.transport.post<toolbox.CompletionList>('/lsp/completions', body)
  }

  public async documentSymbols(uri: string): Promise<LspSymbol[]> {
    const query = encodeQuery({ languageId: this.languageId, pathToProject: this.pathToProject, uri })
    return this.transport.get<toolbox.SymbolInfo[]>(`/lsp/document-symbols${query}`)
  }

  public async workspaceSymbols(query: string): Promise<LspSymbol[]> {
    const search = encodeQuery({ query, languageId: this.languageId, pathToProject: this.pathToProject })
    return this.transport.get<toolbox.SymbolInfo[]>(`/lsp/workspacesymbols${search}`)
  }

  public async stop(): Promise<void> {
    if (this.stopped) return
    try {
      await this.transport.post<void>('/lsp/stop', serverRequest(this.languageId, this.pathToProject))
    } catch (error) {
      if (!(error instanceof DaytonaError) || error.code !== 'LSP_NOT_STARTED') {
        throw error
      }
    }
    this.stopped = true
  }

  public async [Symbol.asyncDispose](): Promise<void> {
    await this.stop()
  }
}

/**
 * Sandbox LSP namespace.
 *
 * Each language server is identified by `languageId` + `pathToProject`.
 * Call `start()` before other operations: an unavailable language server
 * raises `LSP_SERVER_UNAVAILABLE`, and operations on a server that has not
 * started raise `LSP_NOT_STARTED`.
 */
export class LspService {
  constructor(private readonly transport: HttpTransport) {}

  /** Start the language server for a project. */
  public async start(languageId: string, pathToProject: string): Promise<LspServer> {
    await this.transport.post<void>('/lsp/start', serverRequest(languageId, pathToProject))
    return new LspServer(this.transport, languageId, pathToProject)
  }
}
