/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ImagesService } from '../src/v2/Images.js'
import { BadRequestError } from '../src/v2/errors/DaytonaError.js'
import { HttpTransport } from '../src/v2/http.js'
import { artifactApi, imageWire } from './artifactFixtures.js'
import { INTERNAL } from '../src/v2/internal'

function service() {
  const { http } = artifactApi(imageWire())
  return new ImagesService(INTERNAL, http, new HttpTransport('https://api.example'))
}

afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers() })

it.each([
  { onLogs: () => {}, timeout: 100 },
  { onLogs: () => {}, timeoutSeconds: -1 },
  { onLogs: () => {}, timeoutSeconds: NaN },
  { onLogs: () => {}, timeoutSeconds: Infinity },
  { onLogs: () => {}, unknownOption: true },
])('rejects invalid build-log observation options before discovery: %j', async (options) => {
  // Given stale, unknown, or invalid options.
  const images = service()
  const fetch = jest.spyOn(globalThis, 'fetch')
  // When starting observation.
  const pending = images.followBuildLogs('img-one', options)
  // Then validation is typed and no discovery request is sent.
  await expect(pending).rejects.toBeInstanceOf(BadRequestError)
  expect(fetch).not.toHaveBeenCalled()
})

it('converts a fractional observation budget to milliseconds without checkpointing a blocked callback', async () => {
  // Given a valid stream whose consumer has not acknowledged its first bytes.
  jest.useFakeTimers()
  const images = service()
  jest.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ url: 'https://logs.example/build-logs/images/img-one' })))
    .mockResolvedValueOnce(new Response('event: log\nid: first\ndata: {"data_base64":"YQ==","start_offset":"0","next_offset":"1"}\n\n',
      { headers: { 'content-type': 'text/event-stream' } }))
  const onLogs = jest.fn(() => new Promise<void>(() => {}))
  let settled = false
  // When a quarter-second observation starts.
  const pending = images.followBuildLogs('img-one', { onLogs, timeoutSeconds: 0.25 })
  const observed = expect(pending.finally(() => { settled = true }))
    .rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', cursor: undefined })
  // Then it remains active for 249 ms and times out at 250 ms without acknowledging bytes.
  await jest.advanceTimersByTimeAsync(249)
  expect(onLogs).toHaveBeenCalledWith('a')
  expect(settled).toBe(false)
  await jest.advanceTimersByTimeAsync(1)
  await observed
})
