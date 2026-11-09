/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { credentialSafeInspection } from '../diagnostics.js'
import { DaytonaError, createDaytonaError } from './errors/DaytonaError.js'
import { strictUriEncode } from './http.js'
import type { HttpClientOptions, TransportAuth } from './http.js'
import { assertKnownOptions, badRequest, optionKeys } from './options.js'
import { arrayOf, checkResponse } from './responseFields.js'
import { deepFrozen } from './frozen.js'
import type { ResponseFields } from './responseFields.js'
import type { CreateAccessTokenOptions, SandboxAccessToken } from './types.js'

const ACCESS_TOKEN_KEYS = optionKeys<CreateAccessTokenOptions>()({ operations: true, ports: true, ttlSeconds: true })
const REFRESH_LEAD_MS = 60_000

interface SandboxAuthInit {
  readonly id: string
  readonly api?: AxiosInstance
  readonly apiKey?: string
  readonly httpOptions?: HttpClientOptions
  readonly options: {
    readonly accessToken?: string
    readonly accessTokenFromCreate?: boolean
    readonly accessTokenExpiresAt?: string
  }
}

interface TokenPayload {
  readonly access_token?: string
  readonly token_type?: string
  readonly expires_at?: string
  readonly operations?: readonly string[]
  readonly ports?: readonly number[] | null
}

const ACCESS_TOKEN: ResponseFields = { access_token: 'string', token_type: 'string', expires_at: 'string', operations: arrayOf('string'), ports: arrayOf('integer', true) }

function tokenFromPayload(payload: TokenPayload | null | undefined): SandboxAccessToken {
  const token = checkResponse<Required<Omit<TokenPayload, 'ports'>> & TokenPayload>(payload, ACCESS_TOKEN, 'sandbox access token')
  if (token.access_token === '') throw badRequest('sandbox access token response has an empty access_token')
  // An expiry that does not parse would read as never expiring and skip the proactive refresh.
  if (rfc3339EpochMs(token.expires_at) === undefined) throw badRequest('sandbox access token response has an expires_at that is not a timestamp')
  const expiresAt = token.expires_at
  return deepFrozen(credentialSafeInspection({
    accessToken: token.access_token,
    tokenType: token.token_type,
    expiresAt,
    operations: [...token.operations],
    ports: token.ports == null ? undefined : [...token.ports],
  }, () => ({ type: 'SandboxAccessToken', expiresAt })))
}

function mintRouteUnsupported(error: unknown): boolean {
  return error instanceof DaytonaError && (error.statusCode === 405 || error.statusCode === 501)
}

export function rfc3339EpochMs(value: string | undefined): number | undefined {
  if (!value) return undefined
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? undefined : parsed
}

/** Per-handle token ownership, proactive refresh, and single-flight re-minting. */
export class SandboxAuth {
  public readonly transportAuth?: TransportAuth
  private token?: string
  private expiry?: string
  private readonly mintable: boolean
  private mintUnsupported = false
  private mint?: Promise<string>

  constructor(private readonly init: SandboxAuthInit) {
    const { options, api, apiKey, httpOptions } = init
    this.token = options.accessToken
    this.expiry = options.accessTokenFromCreate ? options.accessTokenExpiresAt : undefined
    const callerSupplied = options.accessToken !== undefined && options.accessTokenFromCreate !== true
    // Any API credential mints, as in Python: an API key, or the auth provider
    // of a JWT client (its project token authenticates the mint).
    const hasApiCredential = (apiKey !== undefined && apiKey.length > 0) || httpOptions?.authProvider !== undefined
    this.mintable = !callerSupplied && api !== undefined && hasApiCredential
    if (this.mintable) this.transportAuth = { bearer: () => this.bearer(), onUnauthorized: (failedToken) => this.remint(failedToken) }
  }

  public get accessToken(): string | undefined { return this.token }
  public get accessTokenExpiresAt(): string | undefined { return this.expiry }

  public async createAccessToken(options: CreateAccessTokenOptions = {}): Promise<SandboxAccessToken> {
    assertKnownOptions('CreateAccessTokenOptions', options, ACCESS_TOKEN_KEYS)
    if (!this.init.api) throw createDaytonaError('API is not available for this sandbox', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const ports: unknown = options.ports
    if (ports !== undefined && !Array.isArray(ports)) throw badRequest('ports must be an array of port numbers')
    for (const port of (ports ?? []) as unknown[]) {
      if (typeof port !== 'number' || !Number.isInteger(port) || port < 1 || port > 65535) {
        throw badRequest(`port must be an integer in 1..65535, got ${port}`)
      }
    }
    if (options.ttlSeconds !== undefined && (!Number.isInteger(options.ttlSeconds) || options.ttlSeconds <= 0)) {
      throw badRequest('ttlSeconds must be a positive integer')
    }
    const operations: unknown = options.operations
    if (operations !== undefined && (!Array.isArray(operations) || !operations.every((operation) => typeof operation === 'string'))) {
      throw badRequest('operations must be an array of strings')
    }
    const body: { operations?: string[]; ports?: number[]; ttl_seconds?: number } = {}
    if (options.operations !== undefined) body.operations = [...options.operations]
    if (options.ports !== undefined) body.ports = [...options.ports]
    if (options.ttlSeconds !== undefined) body.ttl_seconds = options.ttlSeconds
    const response = await this.init.api.post<TokenPayload>(`/sandboxes/${strictUriEncode(this.init.id)}/access-token`, body)
    return tokenFromPayload(response.data)
  }

  private fresh(): boolean {
    if (!this.token) return false
    const expiresAtMs = rfc3339EpochMs(this.expiry)
    return expiresAtMs === undefined || expiresAtMs - Date.now() > REFRESH_LEAD_MS
  }

  private async bearer(): Promise<string | undefined> {
    if (this.fresh()) return this.token
    if (this.mintUnsupported) return this.compatBearer()
    try {
      return await this.refresh(undefined)
    } catch (error) {
      if (mintRouteUnsupported(error)) {
        this.mintUnsupported = true
        return this.compatBearer()
      }
      if (error instanceof DaytonaError && error.statusCode === 404) return this.apiCredential()
      // A proactive mint failure must not discard a still-valid held token.
      const expiresAtMs = rfc3339EpochMs(this.expiry)
      if (this.token && expiresAtMs !== undefined && expiresAtMs > Date.now()) return this.token
      throw error
    }
  }

  private async apiCredential(): Promise<string | undefined> {
    if (this.init.apiKey) return this.init.apiKey
    const header = await this.init.httpOptions?.authProvider?.authorizationHeader()
    return header?.replace(/^Bearer\s+/i, '') || undefined
  }

  private async compatBearer(): Promise<string | undefined> {
    const expiresAtMs = rfc3339EpochMs(this.expiry)
    if (this.token && (expiresAtMs === undefined || expiresAtMs > Date.now())) return this.token
    return (await this.apiCredential()) ?? this.token
  }

  private async apiCredentialRetry(failedToken: string | undefined): Promise<string | undefined> {
    let fallback = await this.apiCredential()
    const authProvider = this.init.httpOptions?.authProvider
    if (fallback !== undefined && fallback === failedToken && !this.init.apiKey && authProvider) {
      const header = await authProvider.authorizationHeader({ forceRefresh: true, rejectedAuthorization: `Bearer ${failedToken}` })
      fallback = header.replace(/^Bearer\s+/i, '') || undefined
    }
    if (fallback === undefined || fallback === failedToken) return undefined
    if (failedToken !== undefined && failedToken === this.token) {
      this.token = undefined
      this.expiry = undefined
    }
    return fallback
  }

  private async remint(failedToken: string | undefined): Promise<string | undefined> {
    if (!this.mintable) return undefined
    if (this.mintUnsupported) return this.apiCredentialRetry(failedToken)
    try {
      return await this.refresh(failedToken)
    } catch (error) {
      if (mintRouteUnsupported(error)) {
        this.mintUnsupported = true
        return this.apiCredentialRetry(failedToken)
      }
      if (error instanceof DaytonaError && error.statusCode === 404) return this.apiCredentialRetry(failedToken)
      if (error instanceof DaytonaError && error.statusCode === 403) throw error
      // Keep the original toolbox 401 actionable when a re-mint fails.
      return undefined
    }
  }

  private async refresh(failedToken: string | undefined): Promise<string> {
    if (failedToken === undefined) {
      if (this.fresh() && this.token) return this.token
    } else if (this.token && this.token !== failedToken && this.fresh()) {
      return this.token
    }
    this.mint ??= this.mintNow().finally(() => { this.mint = undefined })
    return this.mint
  }

  private async mintNow(): Promise<string> {
    const minted = await this.createAccessToken()
    this.token = minted.accessToken
    this.expiry = minted.expiresAt
    return minted.accessToken
  }

  public async previewToken(port: number): Promise<SandboxAccessToken | undefined> {
    if (!this.mintable || this.mintUnsupported) return undefined
    try {
      return await this.createAccessToken({ operations: [], ports: [port] })
    } catch (error) {
      if (mintRouteUnsupported(error)) {
        this.mintUnsupported = true
        return undefined
      }
      if (error instanceof DaytonaError && error.statusCode === 404) return undefined
      throw error
    }
  }
}
