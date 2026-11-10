/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The options guard refuses an enumerable symbol key even on Object.prototype:
// its walk goes to the end of the chain, as `for...in` does for string keys.

import { NotFoundError, BadRequestError } from '../src/v2/errors/DaytonaError'
import { assertKnownOptions, optionKeys } from '../src/v2/options'
import { mapComputerUseProcessStatusMap } from '../src/v2/ComputerUseMappers'
import { enumerableSymbolKey } from '../src/v2/symbolKeys'

const key = Symbol('polluted')
const STATUS = { Name: 'x', Running: true, Priority: 1, AutoRestart: false, Failed: false, RestartAttempts: 0, RestartBackoffMs: 0, Status: 'ok' }

describe('a symbol key on Object.prototype', () => {
  beforeEach(() => {
    Object.defineProperty(Object.prototype, key, { value: true, enumerable: true, configurable: true, writable: true })
  })
  afterEach(() => {
    delete (Object.prototype as Record<symbol, unknown>)[key]
  })

  it('is found by the shared walk', () => {
    expect(enumerableSymbolKey({})).toBe(key)
  })

  it('is refused by the options guard', () => {
    const keys = optionKeys<{ readonly a?: number }>()({ a: true })
    expect(() => assertKnownOptions('Options', {}, keys)).toThrow(expect.objectContaining({
      constructor: BadRequestError, message: 'Options has no option `Symbol(polluted)`', code: 'BAD_REQUEST', source: 'SDK',
    }))
  })

  it('refuses inherited error options while leaving response maps working', () => {
    expect(() => new NotFoundError('x', { statusCode: 404 })).toThrow(TypeError)
    expect(Object.keys(mapComputerUseProcessStatusMap({ x: STATUS }))).toEqual(['x'])
  })

  it('still finds an own or class-level symbol in error options', () => {
    const own = Symbol('own')
    expect(() => new NotFoundError('x', { [own]: true } as never)).toThrow(TypeError)
  })
})

describe('without one', () => {
  it('finds none on a plain object or a null-prototype one', () => {
    expect(enumerableSymbolKey({ a: 1 })).toBeUndefined()
    expect(enumerableSymbolKey(Object.create(null) as Record<string, never>)).toBeUndefined()
    expect(Object.getOwnPropertySymbols(Object.prototype)).not.toContain(key)
  })
})
