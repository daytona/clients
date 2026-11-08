/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import type { Sandbox, SandboxConnectOptionsFromCreate } from './Sandbox.js'
import type * as api from './gen/api-types.js'
import { SearchTotalRelation as SEARCH_TOTAL_RELATIONS } from './gen/api-enums.js'
import { knownOrUnknown } from './knownValue.js'
import { strictUriEncode } from './http.js'
import { assertKnownOptions, badRequest, snapshotOptions } from './options.js'
import { CONNECT_KEYS, FIND_KEYS, LIST_KEYS, SEARCH_KEYS } from './clientOptionKeys.js'
import { createRegion, hasText, rejectV0Target } from './clientCreateOptions.js'
import { labelsWithName } from './clientCreateNames.js'
import { SEARCH_PATH, searchPageSandboxes, searchQueryParams } from './clientSearch.js'
import { DaytonaError } from './errors/DaytonaError.js'
import { assertSandboxResponse } from './sandboxMetadata.js'
import type { SearchPageDto } from './clientSearch.js'
import type { FindByNameOptions, ListResult, ListSandboxesOptions, SandboxConnectOptions, SandboxDto, SandboxInfo, SearchResult, SearchSandboxesOptions } from './types.js'
import { isBlank } from './pyText.js'

/** @internal */
export interface SandboxClientContext {
  readonly api: AxiosInstance
  readonly buildSandbox: (source: SandboxDto | SandboxInfo | string, options?: SandboxConnectOptionsFromCreate) => Sandbox
}

const DETAIL_FALLBACK_CONCURRENCY = 8

async function resolveSandboxToolboxSource<T extends SandboxDto>(context: SandboxClientContext, sandbox: T): Promise<T | api.Sandbox> {
  if (hasText(sandbox.toolbox_proxy_url) || hasText(sandbox.toolboxProxyUrl)) return sandbox
  // One unreadable item must not fail the page: the handle keeps no toolbox
  // and says so with the explicit error on first toolbox use.
  // A detail that breaks the contract is unreadable too: the list item stands, as in Python.
  return context.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(sandbox.id)}`, undefined).then((response) => {
    assertSandboxResponse(response.data)
    return response.data
  }).catch((error: unknown) => {
    if (error instanceof DaytonaError) return sandbox
    throw error
  })
}

async function sandboxFromDto(context: SandboxClientContext, sandbox: SandboxDto, options: SandboxConnectOptions = {}): Promise<Sandbox> {
  const source = await resolveSandboxToolboxSource(context, sandbox)
  return context.buildSandbox(source, options)
}

/** Items lacking a toolbox URL each need a detail read: bounded concurrency. */
async function sandboxesFromDtos(context: SandboxClientContext, items: readonly SandboxDto[]): Promise<Sandbox[]> {
  const out: Sandbox[] = new Array(items.length)
  let next = 0
  const worker = async (): Promise<void> => {
    for (let i = next++; i < items.length; i = next++) out[i] = await sandboxFromDto(context, items[i])
  }
  await Promise.all(Array.from({ length: Math.min(DETAIL_FALLBACK_CONCURRENCY, items.length) }, worker))
  return out
}

/** @internal */
export async function getSandbox(context: SandboxClientContext, id: string, options: SandboxConnectOptions): Promise<Sandbox> {
  options = snapshotOptions('SandboxConnectOptions', options, CONNECT_KEYS) ?? {}
  if (options.autoDelete !== undefined && typeof options.autoDelete !== 'boolean') throw badRequest('autoDelete must be a boolean')
  // A blank id would read the sandbox list: refused before any request, as in Python.
  if (typeof id !== 'string' || isBlank(id)) throw badRequest('sandboxId must be a non-empty string')
  const sandbox = await context.api.get<api.Sandbox>(`/sandboxes/${strictUriEncode(id)}`, undefined).then((response) => response.data)
  assertSandboxResponse(sandbox)
  return context.buildSandbox(sandbox, options)
}

/** @internal */
export async function listSandboxes(context: SandboxClientContext, options: ListSandboxesOptions): Promise<ListResult<Sandbox>> {
  options = snapshotOptions('ListSandboxesOptions', options, LIST_KEYS) ?? {}
  const listResponse = await context.api.get<api.ListSandboxesResponse>('/sandboxes', {
    params: { cursor: options.cursor, limit: options.limit },
  }).then((response) => response.data)
  const page = listPage<api.Sandbox>(listResponse, 'sandboxes', 'sandbox list')
  const nextCursor = page.nextCursor ?? ('nextCursor' in listResponse && typeof listResponse.nextCursor === 'string' ? listResponse.nextCursor : undefined)
  page.items.forEach(assertSandboxResponse)
  return { items: await sandboxesFromDtos(context, page.items), nextCursor }
}

/** @internal */
export async function searchSandboxes(context: SandboxClientContext, options: SearchSandboxesOptions): Promise<SearchResult<Sandbox>> {
  options = snapshotOptions('SearchSandboxesOptions', options, SEARCH_KEYS) ?? {}
  const data = await context.api.get<SearchPageDto>(SEARCH_PATH, { params: searchQueryParams(options) }).then((response) => response.data)
  const items = searchPageSandboxes(data)
  const sandboxes = await sandboxesFromDtos(context, items)
  const rawTotalRelation = data.total_relation ?? 'eq'
  return {
    items: sandboxes,
    nextCursor: typeof data.next_cursor === 'string' ? data.next_cursor : undefined,
    total: data.total ?? 0,
    totalRelation: knownOrUnknown(rawTotalRelation, SEARCH_TOTAL_RELATIONS),
    rawTotalRelation,
  }
}

/** @internal */
export async function findSandboxesByName(context: SandboxClientContext, name: string, options: FindByNameOptions): Promise<readonly Sandbox[]> {
  rejectV0Target(options)
  options = snapshotOptions('FindByNameOptions', options, FIND_KEYS) ?? {}
  if (typeof name !== 'string' || isBlank(name)) throw badRequest('name must be a non-blank string')
  const region = createRegion(options)
  const found: Sandbox[] = []
  const seen = new Set<string>()
  let cursor: string | undefined
  for (;;) {
    const page = await context.api.get<SearchPageDto>(SEARCH_PATH, {
      params: { labels: JSON.stringify(labelsWithName(name, undefined)), limit: 100, ...(cursor ? { cursor } : {}) },
      ...(region ? { headers: { 'x-daytona-region': region } } : {}),
    }).then((response) => response.data)
    const live = searchPageSandboxes(page).filter((sandbox) => sandbox.status !== 'deleted' && sandbox.status !== 'deleting')
    const sandboxes = await sandboxesFromDtos(context, live)
    found.push(...sandboxes)
    const nextCursor = page.next_cursor ?? undefined
    if (!nextCursor || seen.has(nextCursor)) return Object.freeze(found)
    seen.add(nextCursor)
    cursor = nextCursor
  }
}
