/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { imageWire } from './artifactFixtures'
import { createServer } from 'node:http'
import type { Server, ServerResponse } from 'node:http'
import { once } from 'node:events'
import { ImagesService } from '../src/v2/Images.js'
import { ImageBuilder } from '../src/v2/Image.js'
import { createHttpClient } from '../src/v2/http.js'
import { RequestTimeoutError, ClientTimeoutError } from '../src/v2/errors/DaytonaError.js'
import { INTERNAL } from '../src/v2/internal'

type Phase = 'post' | 'initial' | 'poll'
let server: Server
let baseUrl: string
let respond: (phase: Phase, response: ServerResponse) => void
let phases: Phase[]
const timers = new Set<ReturnType<typeof setTimeout>>()

function reply(phase: Phase, response: ServerResponse): void {
  response.setHeader('Content-Type', 'application/json')
  response.end(JSON.stringify(phase === 'post'
    ? { id: 'img-pull', status: 'pending' }
    : imageWire({ id: 'img-pull', status: phase === 'initial' ? 'pending' : 'ready' })))
}

beforeEach(async () => {
  phases = []
  respond = reply
  server = createServer((request, response) => {
    const phase = request.method === 'POST' ? 'post' : request.url?.includes('wait=') ? 'poll' : 'initial'
    request.resume()
    phases.push(phase)
    respond(phase, response)
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  baseUrl = `http://127.0.0.1:${address.port}`
})

afterEach(async () => {
  jest.restoreAllMocks()
  for (const timer of timers) clearTimeout(timer)
  timers.clear()
  server.closeAllConnections()
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
})

it.each<Phase>(['post', 'initial', 'poll'])('normalizes a real %s transport timeout before the final business-clock tick', async (stalled) => {
  // Given a real Axios request bounded by the total budget, with the business clock held just before expiry.
  let now = 0
  jest.spyOn(Date, 'now').mockImplementation(() => now)
  respond = (phase, response) => {
    if (phase !== stalled) return reply(phase, response)
    now = 99
  }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 800))
  // When Axios's real timer expires while the business clock still reads 99 of 100ms.
  const result = service.pull('alpine', { timeoutSeconds: 0.1 })
  // Then ownership of the active timeout, not the next clock tick, selects the pull-specific error.
  await expect(result).rejects.toMatchObject({ message: 'image pull did not finish before timeout', source: 'SDK' })
})

it.each([
  { budget: 100, root: 100, normalized: true },
  { budget: 100.2, root: 100.5, normalized: true },
  { budget: 100.2, root: 100, normalized: false },
])('classifies equal/fractional active timeout ownership with budget=$budget and root=$root', async ({ budget, root, normalized }) => {
  // Given equal or fractional competing budgets and a real stalled POST.
  let now = 0
  jest.spyOn(Date, 'now').mockImplementation(() => now)
  respond = () => { now = budget - 0.1 }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, root))
  // When the active Axios timer fires before the held deadline tick.
  const result = service.pull('alpine', { timeoutSeconds: budget / 1000 })
  // Then rounding does not misclassify an overall-bound timeout or a genuinely shorter root timer.
  await expect(result).rejects.toMatchObject(normalized
    ? { message: 'image pull did not finish before timeout', source: 'SDK' }
    : { message: 'operation timed out', statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
})

it.each<Phase>(['post', 'initial', 'poll'])('cancels a stalled %s at the overall deadline', async (stalled) => {
  // Given a real HTTP peer that never answers one phase.
  respond = (phase, response) => { if (phase !== stalled) reply(phase, response) }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 800))
  const start = performance.now()
  // When the total budget is shorter than the root transport timeout.
  const result = service.pull('alpine', { timeoutSeconds: 0.1 })
  // Then every phase uses the pull-specific timeout, promptly rather than root timeout or poll padding.
  await expect(result).rejects.toBeInstanceOf(RequestTimeoutError)
  await expect(result).rejects.toMatchObject({ message: 'image pull did not finish before timeout', source: 'SDK' })
  expect(performance.now() - start).toBeLessThan(600)
})

it.each([false, true])('handles HTTP 408 with after-deadline=%s', async (late) => {
  // Given a real API 408 before or after the overall budget.
  respond = (phase, response) => {
    if (phase !== 'poll') return reply(phase, response)
    const send = () => {
      response.writeHead(408, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ code: 'REQUEST_TIMEOUT', source: 'API', message: 'server timeout' }))
    }
    if (late) timers.add(setTimeout(send, 500))
    else send()
  }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 800))
  const start = performance.now()
  // When pulling with a finite budget.
  const result = service.pull('alpine', { timeoutSeconds: 0.2 })
    // Then only deadline exhaustion is normalized; an independent server 408 is preserved.
  await expect(result).rejects.toMatchObject(late
    ? { message: 'image pull did not finish before timeout', source: 'SDK' }
      : { message: 'server timeout', source: 'API' })
  expect(performance.now() - start).toBeLessThan(400)
})

it('shares the budget across submission, initial lookup, and polling', async () => {
  // Given three HTTP phases that individually fit within the total budget.
  respond = (phase, response) => { timers.add(setTimeout(() => reply(phase, response), 120)) }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 800))
  const start = performance.now()
  // When their cumulative duration exceeds that budget.
  const result = service.pull('alpine', { timeoutSeconds: 0.3 })
  // Then the final phase cannot spend the budget again.
  await expect(result).rejects.toMatchObject({ message: 'image pull did not finish before timeout', source: 'SDK' })
  expect(performance.now() - start).toBeLessThan(500)
})

it('cancels an active response body even when the root transport is unbounded', async () => {
  // Given a poll that continuously sends bytes without completing its JSON body.
  let disconnected: Promise<unknown> | undefined
  respond = (phase, response) => {
    if (phase !== 'poll') return reply(phase, response)
    disconnected = once(response, 'close')
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.write('{')
    for (let chunk = 1; chunk <= 10; chunk++) {
      timers.add(setTimeout(() => response.write(' '), chunk * 30))
    }
  }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 0))
  // When the operation deadline expires during active network traffic.
  const result = service.pull('alpine', { timeoutSeconds: 0.2 })
  // Then cancellation closes the in-flight response, not only a racing promise.
  await expect(result).rejects.toMatchObject({ message: 'image pull did not finish before timeout', source: 'SDK' })
  expect(phases).toEqual(['post', 'initial', 'poll'])
  await disconnected
})

it.each<Phase>(['post', 'initial'])('preserves a root timeout during %s before the deadline', async (stalled) => {
  // Given a root transport budget smaller than the total pull budget.
  respond = (phase, response) => { if (phase !== stalled) reply(phase, response) }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 50))
  // When the transport times out first.
  const result = service.pull('alpine', { timeoutSeconds: 0.3 })
  // Then it is the client's own timeout, not the pull's deadline error.
  await expect(result).rejects.toBeInstanceOf(ClientTimeoutError)
  await expect(result).rejects.toMatchObject({ message: 'operation timed out', statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
})

it.each([undefined, 0, 1])('lets a long poll outlive a shorter request timeout when pull timeout is %s', async (timeoutSeconds) => {
  // Given a real long poll that answers after the 200 ms request timeout.
  respond = (phase, response) => {
    if (phase !== 'poll') return reply(phase, response)
    timers.add(setTimeout(() => reply(phase, response), 500))
  }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 200))
  // When pulling with an omitted, zero, or finite readiness deadline.
  const image = await service.pull('alpine', { timeoutSeconds })
  // Then the poll is governed by the pull budget, not the request timeout.
  expect(image.status).toBe('ready')
  expect(phases).toEqual(['post', 'initial', 'poll'])
})

it('ends a stalled poll at the pull deadline, not at the shorter request timeout', async () => {
  // Given a real stalled readiness poll and a shorter request timeout.
  respond = (phase, response) => { if (phase !== 'poll') reply(phase, response) }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 200))
  // When the pull's one-second readiness budget expires.
  const result = service.pull('alpine', { timeoutSeconds: 1 })
  // Then the pull's own deadline error is raised.
  await expect(result).rejects.toMatchObject({ message: 'image pull did not finish before timeout', statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
})

it('leaves standalone build acceptance-only', async () => {
  // Given a submitted import whose initial lookup is still pending.
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 800))
  // When using build rather than pull.
  const image = await service.build(ImageBuilder.fromRegistry('alpine'))
  // Then no readiness polling is introduced.
  expect(image.status).toBe('pending')
  expect(phases).toEqual(['post', 'initial'])
})

it('keeps a timeout the credential provider raises itself, before the pull deadline', async () => {
  // Given a provider whose own exchange times out at once, well inside the budget.
  const ownTimeout = new ClientTimeoutError('token exchange timed out')
  const authProvider = { authorizationHeader: async (): Promise<string> => { throw ownTimeout } }
  const service = new ImagesService(INTERNAL, createHttpClient(baseUrl, undefined, 800, { authProvider }))
  // When pulling with a budget that the request timeout covers.
  const result = service.pull('alpine', { timeoutSeconds: 0.5 })
  // Then the provider's error is not rewritten as the pull's deadline.
  await expect(result).rejects.toBe(ownTimeout)
  expect(phases).toEqual([])
})
