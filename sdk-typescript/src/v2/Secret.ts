/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * Daytona SDK — SecretsService.
 *
 * Project-scoped secrets: named key/value pairs. Attach one or more to a
 * sandbox at creation through the `secrets` option (a list of names).
 * Values are write-only and restricted to each secret's allowed HTTPS domains;
 * metadata responses never include them.
 *
 * Uses the `/secrets` routes, scoped to the API key's project.
 */

import { listPage } from './listPage.js'
import type { AxiosInstance } from 'axios'
import { credentialSafeDiagnostics } from '../diagnostics.js'
import { assertInternal } from './internal.js'
import type { Internal } from './internal.js'
import type * as api from './gen/api-types.js'
import type { ListResult } from './types.js'
import { strictUriEncode } from './http.js'
import { assertKnownOptions, assertOptionsObject, badRequest, optionKeys } from './options.js'

/** Secret metadata (never the value). */
export interface Secret {
  readonly name: string
  readonly allowedDomains: readonly string[]
  readonly description?: string
  readonly createdAt: string
  readonly updatedAt: string
}

/** Options for creating/updating a secret. */
export interface SetSecretOptions {
  /** The hosts where this secret's value may be sent (exact or `*.` wildcard),
   *  or `["*"]` for any verified host. `"*"` lets the value reach any HTTPS
   *  host, including one a compromised sandbox controls. */
  readonly allowedDomains: readonly string[]
  readonly description?: string
  readonly ifNotExists?: boolean
}

export interface ListSecretsOptions {
  readonly cursor?: string
  readonly limit?: number
}

const SET_SECRET_KEYS = optionKeys<SetSecretOptions>()({ allowedDomains: true, description: true, ifNotExists: true })
const LIST_SECRETS_KEYS = optionKeys<ListSecretsOptions>()({ cursor: true, limit: true })

function mapSecret(secret: api.Secret): Secret {
  return {
    name: secret.name,
    allowedDomains: [...secret.allowed_domains],
    description: secret.description ?? undefined,
    createdAt: secret.created_at,
    updatedAt: secret.updated_at,
  }
}

/** Service for managing project-scoped secrets. */
export class SecretsService {
  /** @internal */
  constructor(token: Internal, api: AxiosInstance)
  /** Built by the SDK: get it from the client or a sandbox. */
  constructor(token: Internal, init: never)
  constructor(token: Internal, private readonly api: AxiosInstance) {
    assertInternal(token, 'SecretsService')
    credentialSafeDiagnostics(this, () => ({ type: 'SecretsService' }))
  }

  /** Lists secret metadata for the project (never values). */
  public async list(options: ListSecretsOptions = {}): Promise<ListResult<Secret>> {
    assertKnownOptions('ListSecretsOptions', options, LIST_SECRETS_KEYS)
    const { data } = await this.api.get<api.ListSecretsResponse>('/secrets', { params: options })
    const page = listPage<api.Secret>(data, 'secrets', 'secret list')
    return { items: page.items.map(mapSecret), nextCursor: page.nextCursor }
  }

  public async *iter(options: ListSecretsOptions = {}): AsyncIterable<Secret> {
    assertKnownOptions('ListSecretsOptions', options, LIST_SECRETS_KEYS)
    let cursor = options.cursor
    const seen = new Set<string>()
    if (cursor !== undefined) seen.add(cursor)
    for (;;) {
      const page = await this.list({ ...options, cursor })
      yield* page.items
      if (!page.nextCursor || page.nextCursor === cursor || seen.has(page.nextCursor)) return
      seen.add(page.nextCursor)
      cursor = page.nextCursor
    }
  }

  /** Gets one secret's metadata (never its value). */
  public async get(name: string): Promise<Secret> {
    const { data } = await this.api.get<api.Secret>(`/secrets/${strictUriEncode(name)}`)
    return mapSecret(data)
  }

  /** Creates or updates a secret's value + metadata. */
  public async set(name: string, value: string, options: SetSecretOptions): Promise<Secret> {
    // The options are required: an omitted value is refused like any non-object.
    assertOptionsObject('SetSecretOptions', options)
    assertKnownOptions('SetSecretOptions', options, SET_SECRET_KEYS)
    if (!Array.isArray(options.allowedDomains) || options.allowedDomains.some((domain) => typeof domain !== 'string')) {
      throw badRequest('allowedDomains must be an array of strings')
    }
    const body: api.PutSecretRequest = { value, allowed_domains: [...options.allowedDomains] }
    if (options.description !== undefined) body.description = options.description
    const config = options.ifNotExists ? { headers: { 'If-None-Match': '*' } } : undefined
    const { data } = await this.api.put<api.Secret>(`/secrets/${strictUriEncode(name)}`, body, config)
    return mapSecret(data)
  }

  public async delete(name: string): Promise<void> {
    await this.api.delete(`/secrets/${strictUriEncode(name)}`)
  }
}
