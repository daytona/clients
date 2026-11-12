/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios from 'axios'
import { inspect } from 'node:util'
import { PortsService } from '../src/v2/Ports.js'
import { INTERNAL } from '../src/v2/internal'

describe('preview result inspection', () => {
  it.each([
    ['endpoint', (ports: PortsService) => ports.expose(8080)],
    ['preview', (ports: PortsService) => ports.previewLink(8080)],
    ['signed preview', (ports: PortsService) => ports.signedPreviewLink(8080)],
  ] as const)('hides the URL and token when inspecting a %s result', async (_name, operation) => {
    // Given
    const url = 'https://sensitive.preview.invalid'
    const token = 'sensitive-preview-token'
    const http = axios.create({ adapter: async (config) => ({ config, status: 200, statusText: 'OK', headers: {},
      data: config.url?.endsWith('/signed-preview-urls') || config.url?.endsWith('/preview-url')
        ? { url, token, expires_at: '2027-01-01T00:00:00Z' }
        : { port: 8080, public: false, url, created_at: '2026-01-01T00:00:00Z' },
    }) })
    // When
    const result = await operation(new PortsService(INTERNAL, undefined, http, 'sandbox'))
    // Then
    expect(result.url).toBe(url)
    expect(JSON.parse(JSON.stringify(result))).toMatchObject({ url })
    for (const shown of [inspect(result), inspect({ result }, { depth: null, showHidden: true })]) {
      expect(shown).not.toContain(url)
      expect(shown).not.toContain(token)
    }
  })

  it.each([false, true])('upserts port visibility when public is %s', async (isPublic) => {
    // Given
    const requests: { method?: string; path?: string; body: unknown }[] = []
    const http = axios.create({ adapter: async (config) => {
      requests.push({ method: config.method, path: config.url, body: JSON.parse(config.data) })
      return { config, status: 200, statusText: 'OK', headers: {},
        data: { port: 8080, public: isPublic, url: 'https://preview.invalid', created_at: '2026-01-01T00:00:00Z' } }
    } })
    // When
    const result = await new PortsService(INTERNAL, undefined, http, 'sandbox').expose(8080, { public: isPublic })
    // Then
    expect(result.public).toBe(isPublic)
    expect(requests).toEqual([{ method: 'put', path: '/sandboxes/sandbox/ports/8080', body: { public: isPublic } }])
  })
})
