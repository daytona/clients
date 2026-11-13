/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { pageParams } from './pageParams.js'
import { pathSegment } from './pathSegment.js'
import { deepFrozen, frozenPage } from './frozen.js'
import { nestedDeeperThan } from './jsonDepth.js'
import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics, credentialSafeInspection } from '../diagnostics.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type * as toolbox from './gen/toolbox-types.js'
import type * as api from './gen/api-types.js'
import { BadRequestError, DaytonaError, createDaytonaError } from './errors/DaytonaError.js'
import { strictUriEncode } from './http.js'
import { HttpTransport } from './http.js'
import type { ListResult, PreviewEndpoint, PreviewLink, SandboxAccessToken, SignedPreviewLink } from './types.js'
import { assertKnownOptions, badRequest, optionKeys, snapshotOptions } from './options.js'
import { arrayOf, checkResponse } from './responseFields.js'
import type { ResponseFields } from './responseFields.js'

/**
 * Request header (and its value) that skips the preview warning page. Send it
 * when a headless browser or another non-interactive client opens a preview URL.
 */
export const PREVIEW_WARNING_SKIP_HEADER = 'X-Daytona-Skip-Preview-Warning'
export const PREVIEW_WARNING_SKIP_HEADER_VALUE = 'true'

const JWT_SEGMENT = /^[A-Za-z0-9_-]+$/

export interface ExposePortOptions {
  readonly public?: boolean
}

export interface SignedPreviewLinkOptions {
  readonly expiresInSeconds?: number
}

export interface ListEndpointsOptions {
  readonly cursor?: string
  readonly limit?: number
}

const EXPOSE_PORT_KEYS = optionKeys<ExposePortOptions>()({ public: true })
const SIGNED_PREVIEW_LINK_KEYS = optionKeys<SignedPreviewLinkOptions>()({ expiresInSeconds: true })
const LIST_ENDPOINTS_KEYS = optionKeys<ListEndpointsOptions>()({ cursor: true, limit: true })

function assertPort(port: number): void {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new BadRequestError(`port must be an integer in 1..65535, got ${port}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
}

/** The sandbox daemon's own ports: the API never makes them preview ports. */
const RESERVED_PREVIEW_PORTS: readonly number[] = Object.freeze([2280, 2281, 22220])

/** @internal A port a preview endpoint, preview URL or access token may name. */
export function assertPreviewPort(port: number): void {
  assertPort(port)
  if (RESERVED_PREVIEW_PORTS.includes(port)) {
    throw badRequest(`port ${port} is reserved for the sandbox daemon and is never a preview port`)
  }
}

function portSegment(port: number): string {
  if (!Number.isInteger(port)) {
    throw new BadRequestError(`port must be an integer, got ${String(port)}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return strictUriEncode(String(port))
}

function assertSignedPreviewTtl(expiresInSeconds: number): void {
  if (!Number.isInteger(expiresInSeconds) || expiresInSeconds <= 0) {
    throw new BadRequestError('expiresInSeconds must be a positive integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
}

const PREVIEW_ENDPOINT: ResponseFields = { port: 'integer', public: 'boolean', url: 'string', created_at: 'timestamp' }

function mapPreviewEndpoint(value: api.PreviewEndpoint): PreviewEndpoint {
  const endpoint = checkResponse<api.PreviewEndpoint>(value, PREVIEW_ENDPOINT, 'preview endpoint')
  return deepFrozen(credentialSafeInspection({
    port: endpoint.port,
    public: endpoint.public,
    url: endpoint.url,
    createdAt: endpoint.created_at,
  }, () => ({ type: 'PreviewEndpoint', port: endpoint.port, public: endpoint.public, createdAt: endpoint.created_at })))
}

function previewLinkValue(url: string, token: string, expiresAt: string | undefined): PreviewLink {
  return deepFrozen(credentialSafeInspection(expiresAt === undefined ? { url, token } : { url, token, expiresAt },
    () => ({ type: 'PreviewLink', expiresAt })))
}

function previewTokenExpiry(token: string): string | undefined {
  const parts = token.split('.')
  if (parts.length !== 3 || parts.some((part) => part.length === 0 || !JWT_SEGMENT.test(part))) {
    return undefined
  }
  let payload: unknown
  try {
    // Read only the standard expiry claim for display, never authorization.
    payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(parts[1].length / 4) * 4, '=')))
  } catch {
    return undefined
  }
  // Nested deeper than every JSON body may be: not a token to read.
  if (payload === null || Array.isArray(payload) || typeof payload !== 'object' || nestedDeeperThan(payload)) {
    return undefined
  }
  const exp = Object.getOwnPropertyDescriptor(payload, 'exp')?.value
  if (typeof exp !== 'number' || !Number.isFinite(exp) || exp <= 0) {
    return undefined
  }
  // Whole milliseconds, floored, exactly as the Python SDK does.
  const expiresAtMs = Math.floor(exp * 1000)
  if (!Number.isFinite(expiresAtMs)) {
    return undefined
  }
  try {
    // A finite but out-of-range timestamp has no representable expiry.
    return new Date(expiresAtMs).toISOString().replace('.000Z', 'Z')
  } catch {
    return undefined
  }
}

/** Sandbox port namespace — what is listening inside the sandbox. */
export class PortsService {
  /** @internal */
  constructor(
    token: Internal,
    transport: HttpTransport | undefined,
    api?: AxiosInstance,
    sandboxId?: string,
    mintPreviewToken?: (port: number) => Promise<SandboxAccessToken | undefined>,
  )
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(
    token: Internal,
    private readonly transport: HttpTransport | undefined,
    private readonly api?: AxiosInstance,
    private readonly sandboxId?: string,
    // Resolves undefined when this handle cannot mint a ports-scoped token
    // (caller-supplied token, no key, or route unavailable).
    private readonly mintPreviewToken?: (port: number) => Promise<SandboxAccessToken | undefined>,
  ) {
    assertInternal(token, 'PortsService')
    credentialSafeDiagnostics(this, () => ({ type: 'PortsService' }))
  }

  /** Every TCP port currently listening in the sandbox. */
  public async list(): Promise<ListResult<number>> {
    const response: unknown = await this.requireToolbox().get<toolbox.PortList>('/port')
    // The contract types each port as an int32 and declares no range.
    const { ports } = checkResponse<{ readonly ports: number[] }>(response, { ports: arrayOf('int32') }, 'port list')
    return frozenPage({ items: [...ports] })
  }

  /**
   * Whether `port` currently has a listener.
   *
   * A port outside 1-65535 is refused with `BadRequestError` before any
   * request. The sandbox answers only for ports 3000-9999 and refuses any
   * other with `BAD_REQUEST`, so a port such as 80 throws rather than
   * resolving to a `false` that reads like "nothing is listening there".
   */
  public async isInUse(port: number): Promise<boolean> {
    assertPort(port)
    const response = await this.requireToolbox().get<unknown>(`/port/${portSegment(port)}/in-use`)
    return checkResponse<toolbox.PortInUseResponse>(response, { isInUse: 'boolean' }, 'port in use').isInUse
  }

  /**
   * One page of the sandbox's preview endpoints.
   *
   * A sandbox that is deleted or being deleted throws `ConflictError` (`CONFLICT`).
   */
  public async listEndpoints(options: ListEndpointsOptions = {}): Promise<ListResult<PreviewEndpoint>> {
    options = snapshotOptions('ListEndpointsOptions', options, LIST_ENDPOINTS_KEYS) ?? {}
    const page = await this.requireApi()
      .get<api.EndpointListResponse>(this.portsPath(), { params: pageParams(options.cursor, options.limit) })
      .then((response) => response.data)
    const endpoints = listPage<api.PreviewEndpoint>(page, 'ports', 'preview endpoint list')
    return frozenPage({ items: endpoints.items.map(mapPreviewEndpoint), nextCursor: endpoints.nextCursor })
  }

  public async *iterEndpoints(options: ListEndpointsOptions = {}): AsyncIterable<PreviewEndpoint> {
    options = snapshotOptions('ListEndpointsOptions', options, LIST_ENDPOINTS_KEYS) ?? {}
    let cursor = options.cursor
    const seen = new Set<string>()
    if (cursor !== undefined) seen.add(cursor)
    for (;;) {
      const page = await this.listEndpoints({ ...options, cursor })
      yield* page.items
      if (!page.nextCursor || page.nextCursor === cursor || seen.has(page.nextCursor)) return
      seen.add(page.nextCursor)
      cursor = page.nextCursor
    }
  }

  /**
   * Register a sandbox port as a preview endpoint.
   *
   * If the port is already exposed, returns the existing endpoint (updating its
   * visibility when `options.public` differs from the current value; the change
   * takes effect immediately on event delivery, at most 30 s if an event is
   * missed). Public endpoints need no credential. Programmatic access to a private one needs the
   * token from {@link previewLink} (see {@link PreviewLink}) or a
   * {@link signedPreviewLink} URL.
   * A sandbox that is deleted or being deleted throws `ConflictError` (`CONFLICT`).
   *
   * @param port TCP port inside the sandbox (1–65535, except the sandbox
   *   daemon's reserved ports 2280, 2281 and 22220).
   * @param options.public When `true` the endpoint is publicly accessible without
   *   any credential.
   * @returns {@link PreviewEndpoint} with the URL.
   */
  public async expose(port: number, options: ExposePortOptions = {}): Promise<PreviewEndpoint> {
    assertKnownOptions('ExposePortOptions', options, EXPOSE_PORT_KEYS)
    assertPreviewPort(port)
    if (options.public !== undefined && typeof options.public !== 'boolean') throw badRequest('public must be a boolean')
    const isPublic = options.public ?? false
    const body: api.SetSandboxPortRequest = { public: isPublic }
    return this.requireApi().put<api.PreviewEndpoint>(this.portPath(port), body).then((response) => mapPreviewEndpoint(response.data))
  }

  /**
   * Preview URL plus access token for one sandbox port.
   *
   * Callers send the token in the `X-Daytona-Preview-Token` header. Public
   * endpoints need no token (`token === ''`). The returned link exposes
   * `expiresAt` when the token expiry is knowable. A client that can
   * mint (a project key, no caller-supplied token) gets a short-lived sandbox
   * access token scoped to exactly this port for private endpoints. Otherwise,
   * or if minting fails, the route token is passed through unchanged.
   *
   * A port that is not exposed yet is exposed first (privately, as
   * {@link expose} does): this call registers a preview endpoint. The sandbox
   * daemon's ports 2280, 2281 and 22220 are never preview ports: they are
   * refused with `BadRequestError` before any request, as by {@link expose}
   * and {@link signedPreviewLink}. A sandbox that is deleted or being deleted
   * throws `ConflictError` (`CONFLICT`).
   */
  public async previewLink(port: number): Promise<PreviewLink> {
    assertPreviewPort(port)
    const endpoint = await this.requireApi().get<api.PreviewEndpoint>(this.portPath(port))
      .then((response) => mapPreviewEndpoint(response.data))
      .catch((error: unknown) => {
        if (error instanceof DaytonaError && error.statusCode === 404) return this.expose(port)
        throw error
      })
    if (endpoint.public) {
      return previewLinkValue(endpoint.url, '', undefined)
    }
    const response = await this.requireApi().post<api.DaytonaPreviewUrl>(`${this.portPath(port)}/preview-url`)
      .then((result) => checkResponse<api.DaytonaPreviewUrl>(result.data, { url: 'string', token: 'string' }, 'preview url'))
    let token = response.token
    let expiresAt = previewTokenExpiry(token)
    // Only a token minted through this callback has known port-scoped provenance.
    // The preview-url contract declares no claim for recognizing such tokens.
    if (this.mintPreviewToken) {
      try {
        const minted = await this.mintPreviewToken(port)
        if (minted) {
          token = minted.accessToken
          expiresAt = minted.expiresAt
        }
      } catch {
        // The preview-url route above already returned a usable token; a
        // failed port-scoped mint (a transient 5xx, a just-stopped sandbox)
        // must not discard it.
      }
    }
    return previewLinkValue(response.url || endpoint.url, token, expiresAt)
  }

  /**
   * Return a self-contained signed URL for one sandbox port.
   *
   * The token is embedded in the hostname, so no extra headers or credentials
   * are needed — the right choice for handing a URL to a browser, a tool, or
   * any client that cannot set request headers.
   *
   * The default lifetime is 60 seconds. Pass a longer `expiresInSeconds` for
   * multi-step use (the deployment caps the maximum, typically 7 days).
   * Expire it early with {@link expireSignedPreviewLinks}, which expires every
   * signed preview link of the sandbox.
   * A sandbox that is deleted, being deleted, failed, lost, or stopped without
   * being persistent throws `ConflictError` (`CONFLICT`).
   *
   * @param port TCP port inside the sandbox (1–65535, except the sandbox
   *   daemon's reserved ports 2280, 2281 and 22220).
   * @param options.expiresInSeconds Requested token lifetime in seconds (positive
   *   integer). Omit for the server default (60 s).
   * @returns {@link SignedPreviewLink} with `url`, `token`, and `expiresAt`.
   *
   * @example
   * ```ts
   * const link = await sandbox.ports.signedPreviewLink(8080, { expiresInSeconds: 300 })
   * // Hand link.url to a browser or another tool — no headers needed.
   * ```
   */
  public async signedPreviewLink(port: number, options: SignedPreviewLinkOptions = {}): Promise<SignedPreviewLink> {
    assertKnownOptions('SignedPreviewLinkOptions', options, SIGNED_PREVIEW_LINK_KEYS)
    assertPreviewPort(port)
    if (options.expiresInSeconds !== undefined) assertSignedPreviewTtl(options.expiresInSeconds)
    const path = `${this.sandboxPath()}/ports/${port}/signed-preview-urls`
    const body = options.expiresInSeconds === undefined ? {} : { expires_in_seconds: options.expiresInSeconds }
    const response = await this.requireApi().post<api.SignedPreviewUrlResponse>(path, body)
      .then((result) => checkResponse<api.SignedPreviewUrlResponse>(result.data, { url: 'string', token: 'string', expires_at: 'timestamp' }, 'signed preview url'))
    return deepFrozen(credentialSafeInspection({ url: response.url, token: response.token, expiresAt: response.expires_at },
      () => ({ type: 'SignedPreviewLink', expiresAt: response.expires_at })))
  }

  /**
   * Expire every signed preview link of the sandbox before its natural expiry.
   *
   * Every signed preview link the sandbox has, on every port, stops working.
   * Links created afterwards work. Unlike minting, expiring still works while
   * the sandbox is being deleted.
   *
   * @example
   * ```ts
   * await sandbox.ports.expireSignedPreviewLinks()
   * ```
   */
  public async expireSignedPreviewLinks(): Promise<void> {
    await this.requireApi().post(`${this.sandboxPath()}/signed-preview-urls/revoke`)
  }

  /** Remove a port's preview endpoint; this still works while the sandbox is being deleted. */
  public async unexpose(port: number): Promise<void> {
    assertPort(port)
    await this.requireApi().delete(this.portPath(port))
  }

  private requireApi(): AxiosInstance {
    if (!this.api) {
      throw createDaytonaError('API is not available for preview endpoint operations', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    }
    return this.api
  }

  private requireToolbox(): HttpTransport {
    if (!this.transport) {
      throw createDaytonaError('toolbox transport is not available for sandbox port inspection', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    }
    return this.transport
  }

  private requireSandboxId(): string {
    if (!this.sandboxId) {
      throw createDaytonaError('sandbox id is required for preview endpoint operations', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    }
    return this.sandboxId
  }

  /** `pathSegment` refuses `.` and `..`, which `strictUriEncode` leaves as they are and a URL resolver would collapse onto another route. */
  private sandboxPath(): string {
    return `/sandboxes/${pathSegment(this.requireSandboxId(), 'sandboxId')}`
  }

  private portsPath(): string {
    return `${this.sandboxPath()}/ports`
  }

  private portPath(port: number): string {
    return `${this.portsPath()}/${portSegment(port)}`
  }
}
