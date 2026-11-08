/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { encodeQuery, HttpTransport } from './http.js'
import { BadRequestError, DaytonaError } from './errors/DaytonaError.js'
import type * as toolbox from './gen/toolbox-types.js'
import { assertInternal, INTERNAL } from './internal.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, optionKeys } from './options.js'

/** A zero-based line / UTF-16 character position in a document. */
export interface LspPosition {
  readonly line: number
  readonly character: number
}

export interface LspRange {
  readonly start: LspPosition
  readonly end: LspPosition
}

export interface LspLocation {
  readonly uri: string
  readonly range: LspRange
}

/** One symbol from `documentSymbols()` / `workspaceSymbols()`. */
export interface LspSymbol {
  readonly name: string
  /** The LSP `SymbolKind` number. */
  readonly kind: number
  readonly location: LspLocation
}

/** Marked-up completion documentation. */
export interface LspMarkupContent {
  /** Typically `'markdown'` or `'plaintext'`. */
  readonly kind: string
  readonly value: string
}

/** One completion candidate. */
export interface LspCompletionItem {
  readonly label: string
  /** The LSP `CompletionItemKind` number. */
  readonly kind?: number
  readonly detail?: string
  readonly documentation?: string | LspMarkupContent
  readonly sortText?: string
  readonly filterText?: string
  readonly insertText?: string
}

export interface LspCompletionList {
  readonly isIncomplete: boolean
  readonly items: readonly LspCompletionItem[]
}

/** How a completion request was triggered. */
export interface LspCompletionContext {
  /** The LSP `CompletionTriggerKind` number. */
  readonly triggerKind: number
  readonly triggerCharacter?: string
}

export interface LspCompletionsOptions {
  readonly context?: LspCompletionContext
}

const COMPLETIONS_OPTION_KEYS = optionKeys<LspCompletionsOptions>()({ context: true })
const COMPLETION_CONTEXT_KEYS = optionKeys<LspCompletionContext>()({ triggerKind: true, triggerCharacter: true })
const POSITION_KEYS = optionKeys<LspPosition>()({ line: true, character: true })

function malformed(description: string): BadRequestError {
  return new BadRequestError(`expected ${description}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function positionFromWire(value: unknown): LspPosition {
  if (!isRecord(value) || typeof value.line !== 'number' || typeof value.character !== 'number') throw malformed('LSP symbol list')
  return { line: value.line, character: value.character }
}

function symbolFromWire(value: toolbox.SymbolInfo): LspSymbol {
  const raw: unknown = value
  if (!isRecord(raw) || typeof raw.name !== 'string' || typeof raw.kind !== 'number' || !isRecord(raw.location)) {
    throw malformed('LSP symbol list')
  }
  const location = raw.location
  if (typeof location.uri !== 'string' || !isRecord(location.range)) throw malformed('LSP symbol list')
  return {
    name: raw.name,
    kind: raw.kind,
    location: {
      uri: location.uri,
      range: { start: positionFromWire(location.range.start), end: positionFromWire(location.range.end) },
    },
  }
}

function symbolsFromWire(value: unknown): LspSymbol[] {
  if (!Array.isArray(value)) throw malformed('LSP symbol list')
  return value.map((item: toolbox.SymbolInfo) => symbolFromWire(item))
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function completionItemFromWire(value: unknown): LspCompletionItem {
  if (!isRecord(value) || typeof value.label !== 'string') throw malformed('LSP completion list')
  const documentation = value.documentation
  const item: LspCompletionItem = {
    label: value.label,
    kind: typeof value.kind === 'number' ? value.kind : undefined,
    detail: optionalString(value.detail),
    documentation: typeof documentation === 'string'
      ? documentation
      : isRecord(documentation) && typeof documentation.kind === 'string' && typeof documentation.value === 'string'
      ? { kind: documentation.kind, value: documentation.value }
      : undefined,
    sortText: optionalString(value.sortText),
    filterText: optionalString(value.filterText),
    insertText: optionalString(value.insertText),
  }
  return Object.fromEntries(Object.entries(item).filter(([, entry]) => entry !== undefined)) as unknown as LspCompletionItem
}

function completionListFromWire(value: toolbox.CompletionList): LspCompletionList {
  const raw: unknown = value
  if (!isRecord(raw) || typeof raw.isIncomplete !== 'boolean' || !Array.isArray(raw.items)) throw malformed('LSP completion list')
  return { isIncomplete: raw.isIncomplete, items: raw.items.map(completionItemFromWire) }
}

function contextPayload(context: LspCompletionContext): toolbox.CompletionContext {
  assertKnownOptions('LspCompletionContext', context, COMPLETION_CONTEXT_KEYS)
  if (!Number.isInteger(context.triggerKind)) throw malformed('LSP completion context: triggerKind must be an integer')
  return context.triggerCharacter === undefined
    ? { triggerKind: context.triggerKind }
    : { triggerKind: context.triggerKind, triggerCharacter: context.triggerCharacter }
}

function serverRequest(languageId: string, pathToProject: string): toolbox.ServerRequest {
  return { languageId, pathToProject }
}

function documentRequest(languageId: string, pathToProject: string, uri: string): toolbox.DocumentRequest {
  return { languageId, pathToProject, uri }
}

function positionPayload(position: LspPosition): toolbox.SymbolPosition {
  assertKnownOptions('LspPosition', position, POSITION_KEYS)
  if (!Number.isInteger(position.line) || position.line < 0) {
    throw new BadRequestError('expected LSP position: line must be a non-negative integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  if (!Number.isInteger(position.character) || position.character < 0) {
    throw new BadRequestError('expected LSP position: character must be a non-negative integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return { line: position.line, character: position.character }
}

/** A started language server for one project, returned by `lsp.start()`. */
export class LspServer {
  private stopped = false
  private readonly transport: HttpTransport
  private readonly languageId: string
  private readonly pathToProject: string

  constructor(token: Internal, transport: HttpTransport, languageId: string, pathToProject: string) {
    assertInternal(token, 'LspServer')
    this.transport = transport
    this.languageId = languageId
    this.pathToProject = pathToProject
  }

  /** Notify the server that the document at `uri` was opened. */
  public async openDocument(uri: string): Promise<void> {
    await this.transport.post<void>('/lsp/did-open', documentRequest(this.languageId, this.pathToProject, uri))
  }

  /** Notify the server that the document at `uri` was closed. */
  public async closeDocument(uri: string): Promise<void> {
    await this.transport.post<void>('/lsp/did-close', documentRequest(this.languageId, this.pathToProject, uri))
  }

  public async completions(uri: string, position: LspPosition, options: LspCompletionsOptions = {}): Promise<LspCompletionList> {
    assertKnownOptions('LspCompletionsOptions', options, COMPLETIONS_OPTION_KEYS)
    const body: toolbox.CompletionRequest = {
      languageId: this.languageId,
      pathToProject: this.pathToProject,
      uri,
      position: positionPayload(position),
    }
    if (options.context !== undefined) {
      body.context = contextPayload(options.context)
    }
    return completionListFromWire(await this.transport.post<toolbox.CompletionList>('/lsp/completions', body))
  }

  public async documentSymbols(uri: string): Promise<LspSymbol[]> {
    const query = encodeQuery({ languageId: this.languageId, pathToProject: this.pathToProject, uri })
    return symbolsFromWire(await this.transport.get<toolbox.SymbolInfo[]>(`/lsp/document-symbols${query}`))
  }

  public async workspaceSymbols(query: string): Promise<LspSymbol[]> {
    const search = encodeQuery({ query, languageId: this.languageId, pathToProject: this.pathToProject })
    return symbolsFromWire(await this.transport.get<toolbox.SymbolInfo[]>(`/lsp/workspacesymbols${search}`))
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
  private readonly transport: HttpTransport

  constructor(token: Internal, transport: HttpTransport) {
    assertInternal(token, 'LspService')
    this.transport = transport
  }

  /** Start the language server for a project. */
  public async start(languageId: string, pathToProject: string): Promise<LspServer> {
    await this.transport.post<void>('/lsp/start', serverRequest(languageId, pathToProject))
    return new LspServer(INTERNAL, this.transport, languageId, pathToProject)
  }
}
