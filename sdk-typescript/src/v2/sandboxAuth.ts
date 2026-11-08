/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosInstance } from 'axios'
import { credentialSafeInspection } from '../diagnostics.js'
import { DaytonaError } from './errors/DaytonaError.js'
import { strictUriEncode } from './http.js'
import type { HttpClientOptions, TransportAuth } from './http.js'
import { assertKnownOptions, optionKeys } from './options.js'
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

function tokenFromPayload(payload: TokenPayload | null | undefined): SandboxAccessToken {
  const accessToken = payload?.access_token
  const expiresAt = payload?.expires_at
  if (typeof accessToken !== 'string' || accessToken.length === 0 || typeof expiresAt !== 'string') {
    throw new DaytonaError('API returned an invalid access token payload', { statusCode: 500 })
  }
  return credentialSafeInspection({
    accessToken,
    tokenType: typeof payload?.token_type === 'string' ? payload.token_type : 'Bearer',
    expiresAt,
    operations: Array.isArray(payload?.operations) ? payload.operations.filter((operation): operation is string => typeof operation === 'string') : [],
    ports: Array.isArray(payload?.ports) ? payload.ports.filter((port): port is number => Number.isInteger(port)) : undefined,
  }, () => ({ type: 'SandboxAccessToken', expiresAt }))
}

function mintRouteUnsupported(error: unknown): boolean {
  return error instanceof DaytonaError && (error.statusCode === 404 || error.statusCode === 405 || error.statusCode === 501)
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
    const hasCreationToken = options.accessTokenFromCreate === true && options.accessToken !== undefined
    const hasApiCredential = (apiKey !== undefined && apiKey.length > 0) || (httpOptions?.authProvider !== undefined && hasCreationToken)
    this.mintable = !callerSupplied && api !== undefined && hasApiCredential
    if (this.mintable) this.transportAuth = { bearer: () => this.bearer(), onUnauthorized: (failedToken) => this.remint(failedToken) }
  }

  public get accessToken(): string | undefined { return this.token }
  public get accessTokenExpiresAt(): string | undefined { return this.expiry }

  public async createAccessToken(options: CreateAccessTokenOptions = {}): Promise<SandboxAccessToken> {
    assertKnownOptions('CreateAccessTokenOptions', options, ACCESS_TOKEN_KEYS)
    if (!this.init.api) throw new DaytonaError('API is not available for this sandbox', { statusCode: 400, code: 'UNSUPPORTED_OPERATION' })
    for (const port of options.ports ?? []) {
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        throw new DaytonaError(`port must be an integer in 1..65535, got ${port}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
      }
    }
    if (options.ttlSeconds !== undefined && (!Number.isInteger(options.ttlSeconds) || options.ttlSeconds <= 0)) {
      throw new DaytonaError('ttlSeconds must be a positive integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
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
      if (error instanceof DaytonaError && error.statusCode === 403) throw error
      // Keep the original toolbox 401 actionable when a re-mint fails.
      return undefined
    }
  }

  private async refresh(failedToken: string | undefined): Promise<string> {
    if (failedToken === undefined) {
      if (this.fresh() && this.token) return this.token
    } else if (this.token && this.token !== failedToken) {
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
      throw error
    }
  }
}
