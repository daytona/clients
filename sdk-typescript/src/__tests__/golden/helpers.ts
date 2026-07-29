// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from 'node:crypto'
import { expect } from '@jest/globals'

import { Daytona } from '../../Daytona'
import { Sandbox } from '../../Sandbox'
import { DaytonaError } from '../../errors/DaytonaError'

export const GOLDEN_TIMEOUT_MS = 240000
const POLL_INTERVAL_MS = 250
const POLL_TIMEOUT_MS = 30000

export function createGoldenDaytona(): Daytona {
  return new Daytona()
}

export function createGoldenSandboxName(suffix: string): string {
  return `sdk-ts-golden-${suffix}-${Date.now()}-${randomUUID().slice(0, 8)}`
}

export async function createGoldenSandbox(daytona: Daytona, suffix: string): Promise<Sandbox> {
  // The assertions below pin image-specific behavior (zsh resolution, $HOME,
  // python availability), so the suite must run on the same image everywhere.
  // DAYTONA_GOLDEN_SNAPSHOT lets a local stack point at the production image.
  const snapshot = process.env.DAYTONA_GOLDEN_SNAPSHOT
  return await daytona.create({
    name: createGoldenSandboxName(suffix),
    ...(snapshot ? { snapshot } : {}),
    labels: {
      purpose: 'golden-contract-test',
      suite: suffix,
    },
  })
}

export async function deleteGoldenSandbox(daytona: Daytona, sandbox: Sandbox | undefined): Promise<void> {
  if (!sandbox) {
    return
  }

  try {
    await daytona.delete(sandbox)
  } catch (error) {
    console.warn(`golden cleanup: failed to delete sandbox ${sandbox.id}:`, error)
  }
}

export async function waitForValue<T>(
  readValue: () => Promise<T>,
  isDone: (value: T) => boolean,
  timeoutMs = POLL_TIMEOUT_MS,
): Promise<T> {
  const startedAt = Date.now()
  let currentValue = await readValue()

  while (!isDone(currentValue)) {
    if (Date.now() - startedAt > timeoutMs) {
      throw new Error(`Timed out after ${timeoutMs}ms while waiting for live sandbox state`)
    }

    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    currentValue = await readValue()
  }

  return currentValue
}

export function expectDaemonErrorShape(
  error: unknown,
  expected: {
    readonly message: string | RegExp
    readonly statusCode: number
    readonly code: string
    readonly source: string
  },
): asserts error is DaytonaError {
  expect(error).toBeInstanceOf(DaytonaError)

  const daytonaError = error as DaytonaError
  expect(daytonaError.statusCode).toBe(expected.statusCode)
  expect(daytonaError.code).toBe(expected.code)
  expect(daytonaError.source).toBe(expected.source)

  if (typeof expected.message === 'string') {
    expect(daytonaError.message).toBe(expected.message)
  } else {
    expect(daytonaError.message).toMatch(expected.message)
  }
}

export async function collectAsyncError(run: () => Promise<unknown>): Promise<unknown> {
  try {
    await run()
  } catch (error) {
    return error
  }

  throw new Error('Expected promise to reject, but it resolved successfully')
}

export async function callWithUntypedArgs<Result>(
  fn: (...args: never[]) => Promise<Result>,
  thisArg: unknown,
  args: readonly unknown[],
): Promise<Result> {
  return await Reflect.apply(fn, thisArg, args) as Promise<Result>
}

export async function withProcessLanguage<Result>(
  sandbox: Sandbox,
  language: string,
  run: () => Promise<Result>,
): Promise<Result> {
  const originalLanguage = Reflect.get(sandbox.process, 'language')
  Reflect.set(sandbox.process, 'language', language)

  try {
    return await run()
  } finally {
    Reflect.set(sandbox.process, 'language', originalLanguage)
  }
}
