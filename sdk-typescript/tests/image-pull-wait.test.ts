/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import axios, { AxiosHeaders } from 'axios'
import { ImagesService } from '../src/v2/Images.js'
import { INTERNAL } from '../src/v2/internal'
import { imageWire } from './artifactFixtures'
import { BadRequestError, ImageBuildFailedError, RequestTimeoutError } from '../src/v2/errors/DaytonaError.js'

function importApi(statuses: string[]) {
  const waits: (string | undefined)[] = []
  const http = axios.create({ adapter: async (config) => {
    waits.push(config.params?.wait)
    const data = config.method === 'post'
      ? { id: 'img-pull', status: 'pending' }
      : imageWire({ id: 'img-pull', status: statuses.shift() ?? 'ready', error: 'registry denied' })
    return { data, status: 200, statusText: 'OK', headers: new AxiosHeaders(), config }
  } })
  return { http, waits }
}

afterEach(() => jest.restoreAllMocks())

it('returns ready when the registry import is initially pending', async () => {
  // Given an import remaining nonterminal across a long poll.
  const { http, waits } = importApi(['pending', 'building', 'ready'])
  // When pulling with default options.
  const image = await new ImagesService(INTERNAL, http).pull('python:3.12')
  // Then the call completes at readiness, using the existing long poll.
  expect(image.status).toBe('ready')
  expect(waits.slice(2)).toEqual(['ready', 'ready'])
})

it('returns pending when wait is disabled', async () => {
  // Given a pending import.
  const { http, waits } = importApi(['pending'])
  const options = { name: 'test', wait: false }
  // When opting out of waiting.
  const image = await new ImagesService(INTERNAL, http).pull('python:3.12', options)
  // Then only submission and the initial read were needed.
  expect(image.status).toBe('pending')
  expect(waits).toHaveLength(2)
})

it('waits without a deadline when timeout is zero', async () => {
  // Given an import with multiple nonterminal observations.
  const { http } = importApi(['pending', 'building', 'ready'])
  // When zero requests an unbounded wait.
  const image = await new ImagesService(INTERNAL, http).pull('python:3.12', { timeoutSeconds: 0 })
  // Then readiness still completes the call.
  expect(image.status).toBe('ready')
})

it.each([-1, NaN, Infinity])('refuses invalid timeout %s before submission', async (timeoutSeconds) => {
  // Given a caller-supplied invalid budget.
  const { http, waits } = importApi(['pending'])
  // When pulling with that budget.
  await expect(new ImagesService(INTERNAL, http).pull('python:3.12', { timeoutSeconds })).rejects.toBeInstanceOf(BadRequestError)
  // Then the invalid request never crosses the network boundary.
  expect(waits).toEqual([])
})

it.each([['failed'], ['pending', 'failed'], ['error'], ['pending', 'error']])('raises the server failure when import statuses are %j', async (...statuses: string[]) => {
  // Given an immediate or delayed import failure.
  const { http } = importApi(statuses)
  // When pulling to readiness.
  const result = new ImagesService(INTERNAL, http).pull('python:3.12')
  // Then the typed failure retains the server message and image identity.
  await expect(result).rejects.toBeInstanceOf(ImageBuildFailedError)
  await expect(result).rejects.toMatchObject({ imageId: 'img-pull', buildError: 'registry denied' })
})

it('raises a timeout when submission and long polls exhaust the budget', async () => {
  // Given repeated nonterminal reads and a deterministic clock.
  const { http, waits } = importApi(['pending', 'building', 'ready'])
  jest.spyOn(Date, 'now').mockImplementation(() => waits.length * 1000)
  const get = jest.spyOn(http, 'get')
  const options = { name: 'test', timeoutSeconds: 3 }
  // When pulling with a three-second budget.
  await expect(new ImagesService(INTERNAL, http).pull('python:3.12', options)).rejects.toBeInstanceOf(RequestTimeoutError)
  // Then only one second remained for the long poll.
  expect(get.mock.calls[1]?.[1]?.params).toEqual({ wait: 'ready', timeout_ms: 1000 })
})

it.each(['initial', 'poll'].flatMap(phase => ['ready', 'failed', 'error'].map(status => ({ phase, status }))))('rejects a late terminal $phase $status even before the timer callback runs', async ({ phase, status }) => {
  // Given an adapter returning a terminal response after advancing a deterministic clock.
  let now = 0
  jest.spyOn(Date, 'now').mockImplementation(() => now)
  const http = axios.create({ adapter: async (config) => {
    const terminal = config.method === 'get' && (phase === 'initial' || config.params?.wait === 'ready')
    const data = config.method === 'post' ? { id: 'img-pull', status: 'pending' } : imageWire({ id: 'img-pull', status: terminal ? status : 'pending' })
    if (terminal) now = 101
    return { data, status: 200, statusText: 'OK', headers: new AxiosHeaders(), config }
  } })
  // When the response arrives beyond the absolute deadline.
  const result = new ImagesService(INTERNAL, http).pull('alpine', { timeoutSeconds: 0.1 })
  // Then neither success nor build failure can win over deadline expiry.
  await expect(result).rejects.toMatchObject({ message: 'image pull did not finish before timeout', source: 'SDK' })
})
