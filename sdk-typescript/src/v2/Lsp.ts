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
import { bool, int, malformedResponse, nonNegativeInt, optional, record, str } from './toolboxModels.js'
import type { WireRecord } from './toolboxModels.js'
import { deepFrozen } from './frozen.js'

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

function badInput(message: string): BadRequestError {
  return new BadRequestError(message, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

function positionFromWire(value: unknown, description: string): LspPosition {
  const wire = record(value, description)
  return { line: nonNegativeInt(wire, 'line', description), character: nonNegativeInt(wire, 'character', description) }
}

function symbolFromWire(value: unknown): LspSymbol {
  const d = 'LSP symbol'
  const wire = record(value, d)
  const location = record(wire.location, `${d}: location`)
  const range = record(location.range, `${d}: location.range`)
  return {
    name: str(wire, 'name', d),
    kind: int(wire, 'kind', d),
    location: {
      uri: str(location, 'uri', `${d}: location`),
      range: { start: positionFromWire(range.start, `${d}: range.start`), end: positionFromWire(range.end, `${d}: range.end`) },
    },
  }
}

function symbolsFromWire(value: unknown): LspSymbol[] {
  if (!Array.isArray(value)) throw malformedResponse('LSP symbol list')
  return deepFrozen(value.map(symbolFromWire))
}

function documentationFromWire(wire: WireRecord, d: string): string | LspMarkupContent | undefined {
  const documentation = wire.documentation
  if (documentation === undefined || documentation === null) return undefined
  if (typeof documentation === 'string') return documentation
  if (typeof documentation !== 'object' || Array.isArray(documentation)) {
    throw malformedResponse(`${d}: documentation must be a string or a markup content with kind and value`)
  }
  const markup = record(documentation, `${d}: documentation`)
  return { kind: str(markup, 'kind', `${d}: documentation`), value: str(markup, 'value', `${d}: documentation`) }
}

function completionItemFromWire(value: unknown): LspCompletionItem {
  const d = 'LSP completion item'
  const wire = record(value, d)
  const item: LspCompletionItem = {
    label: str(wire, 'label', d),
    kind: optional(wire, 'kind', int, d),
    detail: optional(wire, 'detail', str, d),
    documentation: documentationFromWire(wire, d),
    sortText: optional(wire, 'sortText', str, d),
    filterText: optional(wire, 'filterText', str, d),
    insertText: optional(wire, 'insertText', str, d),
  }
  return Object.fromEntries(Object.entries(item).filter(([, entry]) => entry !== undefined)) as unknown as LspCompletionItem
}

function completionListFromWire(value: toolbox.CompletionList): LspCompletionList {
  const d = 'LSP completion list'
  const wire = record(value, d)
  const items = wire.items
  if (!Array.isArray(items)) throw malformedResponse(`${d}: items is required`)
  return deepFrozen({ isIncomplete: bool(wire, 'isIncomplete', d), items: items.map(completionItemFromWire) })
}

function contextPayload(context: LspCompletionContext): toolbox.CompletionContext {
  assertKnownOptions('LspCompletionContext', context, COMPLETION_CONTEXT_KEYS)
  if (!Number.isInteger(context.triggerKind)) throw badInput('LSP completion context: triggerKind must be an integer')
  if (context.triggerCharacter !== undefined && typeof context.triggerCharacter !== 'string') {
    throw badInput('LSP completion context: triggerCharacter must be a string')
  }
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

  /** @internal */
  constructor(token: Internal, transport: HttpTransport, languageId: string, pathToProject: string)
  /** Built by the SDK: get it from `sandbox.lsp.start()`. */
  constructor(token: Internal, ...init: never[])
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

  /** @internal */
  constructor(token: Internal, transport: HttpTransport)
  /** Built by the SDK: get it from `sandbox.lsp`. */
  constructor(token: Internal, ...init: never[])
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
