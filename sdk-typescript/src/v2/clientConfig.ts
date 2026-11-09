/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { AuthenticationError, BadRequestError } from './errors/DaytonaError.js'
import { createPlatformTokenProvider, DEFAULT_REQUEST_TIMEOUT_MS } from './http.js'
import type { HttpClientOptions } from './http.js'
import { assertKnownOptions, badRequest, boundedTimerDelay, optionalSeconds, snapshotOptions } from './options.js'
import { SANDBOX_EVENTS_CONFIG_KEYS, V2_CONFIG_KEYS } from './types.js'
import type { DaytonaConfig, SandboxEventsConfig } from './types.js'
import { DEFAULT_MAX_RESPONSE_BYTES, maxResponseBytesOption } from './bodyLimit.js'

/**
 * The default Daytona Cloud v2 API, used unless `DAYTONA_V2_API_URL` (a
 * regional endpoint or a test environment) selects another one. `DAYTONA_API_URL` belongs to
 * the 0.x SDK; SDK 2.x never reads it.
 */
export const DEFAULT_V2_API_URL = 'https://api.daytona.com'

function env(name: string): string | undefined {
  const runtime = globalThis as {
    readonly process?: {
      readonly env?: Readonly<Record<string, string | undefined>>
    }
  }
  return runtime.process?.env?.[name]
}

function optionalNonBlankEnv(name: string): string | undefined {
  const value = env(name)
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined
}

function envFlag(name: string): boolean | undefined {
  const value = env(name)
  if (value === undefined) return undefined
  const normalized = value.trim().toLowerCase()
  return normalized === 'true' || normalized === '1'
}

function assertPresentStringOption(name: 'apiKey' | 'jwtToken' | 'projectId', value: string | undefined): void {
  if (value !== undefined && (typeof value !== 'string' || value.length === 0)) {
    throw new BadRequestError(`${name} must be a non-empty string when provided`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
}

function connectionPoolMaxsize(value: number | undefined): number | undefined {
  if (value === undefined) return undefined
  if (!Number.isInteger(value) || value <= 0) {
    throw new BadRequestError('connectionPoolMaxsize must be a positive integer', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  return value
}

// A 128-bit digest of the JWT for the hub identity (two independent 64-bit
// FNV-1a passes). WebCrypto's SHA-256 is async and the identity is needed
// synchronously; this only has to separate tokens within one process, and
// it retains nothing — no table of tokens outlives the clients.
const FNV_PRIME = 0x100000001b3n
const MASK_64 = 0xffffffffffffffffn
function jwtIdentity(token: string): string {
  const bytes = new TextEncoder().encode(token)
  let a = 0xcbf29ce484222325n
  let b = 0x84222325cbf29ce4n
  for (const byte of bytes) {
    a = ((a ^ BigInt(byte)) * FNV_PRIME) & MASK_64
    b = ((b ^ BigInt(byte ^ 0x5c)) * FNV_PRIME) & MASK_64
  }
  return a.toString(16).padStart(16, '0') + b.toString(16).padStart(16, '0')
}

interface ClientConnectionConfig {
  readonly apiUrl: string
  readonly apiKey?: string
  readonly requestTimeoutMs: number
  readonly httpOptions: HttpClientOptions
  readonly hubIdentityKey: string
  readonly events?: SandboxEventsConfig
}

/** @internal */
export function clientConnectionConfig(config: DaytonaConfig): ClientConnectionConfig {
  // Unsupported options must fail loudly for plain-JS callers (TypeScript callers
  // get a compile error), as Python's v2 config rejects them.
  for (const unsupported of ['apiUrl', 'target', 'toolboxUrl']) {
    if (unsupported in config) {
      throw badRequest(
        unsupported === 'apiUrl'
          ? "the v2 client does not take 'apiUrl': set DAYTONA_V2_API_URL to select an API endpoint"
          : unsupported === 'target'
            ? "the v2 client does not take 'target': pass 'region' to each create"
            : "the v2 client does not take 'toolboxUrl': each sandbox carries its own toolbox URL",
      )
    }
  }
  assertKnownOptions('DaytonaConfig', config, V2_CONFIG_KEYS)
  const events = snapshotOptions('SandboxEventsConfig', config.events, SANDBOX_EVENTS_CONFIG_KEYS)
  // Only DAYTONA_V2_API_URL selects the endpoint (DAYTONA_API_URL belongs to
  // the 0.x SDK); read through the computed-key env lookup (bundler-safe).
  // Ambient values are trimmed and a blank one is unset; a slash-only URL
  // is unset too.
  const apiUrl = optionalNonBlankEnv('DAYTONA_V2_API_URL')?.replace(/\/+$/, '') || DEFAULT_V2_API_URL
  // Explicit values are used as given; an explicitly empty one is an error,
  // never "unset". An explicit credential wins over the ambient environment:
  // config.jwtToken suppresses DAYTONA_API_KEY and config.apiKey suppresses
  // DAYTONA_JWT_TOKEN, so an exported key never collides with the credential
  // the caller chose.
  assertPresentStringOption('apiKey', config.apiKey)
  assertPresentStringOption('jwtToken', config.jwtToken)
  assertPresentStringOption('projectId', config.projectId)
  const apiKey = config.apiKey ?? (config.jwtToken !== undefined ? undefined : optionalNonBlankEnv('DAYTONA_API_KEY'))
  const jwtToken = config.jwtToken ?? (config.apiKey !== undefined ? undefined : optionalNonBlankEnv('DAYTONA_JWT_TOKEN'))
  const projectId = config.projectId ?? optionalNonBlankEnv('DAYTONA_PROJECT_ID')
  const requestTimeoutSeconds = optionalSeconds('requestTimeoutSeconds', config.requestTimeoutSeconds)
  // Above the timer maximum (about 24.8 days) means no bound, like 0.
  const requestTimeoutMs = requestTimeoutSeconds === undefined ? DEFAULT_REQUEST_TIMEOUT_MS : boundedTimerDelay(requestTimeoutSeconds * 1000)
  const otelEnabled = config.otelEnabled ?? envFlag('DAYTONA_OTEL_ENABLED') ?? false
  const poolMaxsize = connectionPoolMaxsize(config.connectionPoolMaxsize)
  const maxResponseBytes = maxResponseBytesOption('maxResponseBytes', config.maxResponseBytes) ?? DEFAULT_MAX_RESPONSE_BYTES
  if (!apiKey && !jwtToken) {
    throw new AuthenticationError('configure apiKey or jwtToken (set config.* or DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)', { statusCode: 401, code: 'UNAUTHORIZED', source: 'SDK' })
  }
  if (apiKey && jwtToken) {
    throw new BadRequestError('configure exactly one of apiKey or jwtToken (set config.* or DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  if (jwtToken && !projectId) {
    throw new BadRequestError('projectId is required when jwtToken is set (set config.projectId or DAYTONA_PROJECT_ID)', { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  const base = apiUrl.replace(/\/+$/, '') || DEFAULT_V2_API_URL
  // Hub registry identity: the API key, or for JWT auth the project plus an
  // opaque per-token id. The token must be part of it — two users' JWTs for
  // one project must not share a hub that mints with the first one's
  // credentials — but the registry key never contains the raw JWT.
  const hubIdentityKey = apiKey ?? (jwtToken && projectId ? `jwt:${projectId}:${jwtIdentity(jwtToken)}` : '')
  const httpOptions: HttpClientOptions = {
    authProvider: jwtToken && projectId ? createPlatformTokenProvider({
      baseURL: base,
      jwtToken,
      projectId,
      requestTimeout: requestTimeoutMs,
      otelEnabled,
      otelApi: config.otelApi,
      maxResponseBytes,
    }) : undefined,
    connectionPoolMaxsize: poolMaxsize,
    maxResponseBytes,
    otelEnabled,
    otelApi: config.otelApi,
  }
  return { apiUrl: base, apiKey: apiKey ?? undefined, requestTimeoutMs, httpOptions, hubIdentityKey, events }
}
