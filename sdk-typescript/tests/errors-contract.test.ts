/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The v2 error surface: names, constructors, defaults and the codes catalog.
// sdk-python/tests/test_error_contract.py asserts the same facts.

import { INTERNAL } from '../src/v2/internal'
import * as root from '../src/index'
import * as v2 from '../src/v2/index'
import { errorFromEnvelopeBody } from '../src/v2/errors/DaytonaError'
import { HttpTransport } from '../src/v2/http'
import { GitService } from '../src/v2/Git'
import { ProcessService } from '../src/v2/Process'

describe('v2 error surface', () => {
  it('uses the same category names as the Python SDK', () => {
    expect(new v2.AuthenticationError('x')).toBeInstanceOf(root.DaytonaAuthenticationError)
    expect(new v2.ForbiddenError('x')).toBeInstanceOf(root.DaytonaForbiddenError)
    expect(new v2.ClientTimeoutError('x')).toBeInstanceOf(root.DaytonaTimeoutError)
    expect(new v2.RequestTimeoutError('x')).toBeInstanceOf(root.DaytonaTimeoutError)
    expect(new v2.ClientTimeoutError('x')).not.toBeInstanceOf(v2.RequestTimeoutError)
    expect(new v2.GitAuthFailedError('x')).toBeInstanceOf(v2.AuthenticationError)
    expect(errorFromEnvelopeBody('{}', 'application/json', 401, 'x').constructor).toBe(v2.AuthenticationError)
    // Shared with the Python SDK's test_build_logs.py: a JSON body nested past
    // 600 levels is unreadable, as on the axios and fetch paths.
    const nested = (depth: number) => `{"code":"NOT_FOUND","message":"gone","details":${'['.repeat(depth - 1)}${']'.repeat(depth - 1)}}`
    expect(errorFromEnvelopeBody(nested(600), 'application/json', 404, 'build log HTTP 404')).toMatchObject({ code: 'NOT_FOUND', message: 'gone' })
    const tooDeep = errorFromEnvelopeBody(nested(601), 'application/json', 404, 'build log HTTP 404')
    expect(tooDeep).toBeInstanceOf(v2.NotFoundError)
    expect(tooDeep).toMatchObject({ statusCode: 404, code: undefined, message: 'HTTP 404: the response body was nested too deeply to read (over 600 levels of JSON)' })
    expect(errorFromEnvelopeBody('{}', 'application/json', 403, 'x').constructor).toBe(v2.ForbiddenError)
  })

  it.each([
    'DaytonaAuthenticationError',
    'DaytonaAuthorizationError',
    'DaytonaConflictError',
    'DaytonaConnectionError',
    'DaytonaNotFoundError',
    'DaytonaNotSupportedError',
    'DaytonaRateLimitError',
    'DaytonaTimeoutError',
    'DaytonaValidationError',
    'AuthorizationError',
    'TimeoutError',
  ])('exports no legacy synonym %s (nor its V2 alias)', (name) => {
    expect(Object.keys(v2)).not.toContain(name)
    expect(Object.keys(v2)).not.toContain(`${name}V2`)
  })

  it('keeps DaytonaError and DaytonaErrorV2 as the v2 base', () => {
    expect(v2.DaytonaErrorV2).toBe(v2.DaytonaError)
  })

  it('carries only code, not a second spelling', () => {
    const error = new v2.NotFoundError('missing', { statusCode: 404, code: 'NOT_FOUND', source: 'DAYTONA_API' })
    expect(error.code).toBe('NOT_FOUND')
    expect('errorCode' in error).toBe(false)
  })

  it('takes the message and an options object', () => {
    const cause = new Error('socket closed')
    const error = new v2.ConflictError('busy', { statusCode: 409, code: 'CONFLICT', source: 'DAYTONA_API', traceId: 't-1', retryAfterSeconds: 3, cause })
    expect(error).toMatchObject({ message: 'busy', statusCode: 409, code: 'CONFLICT', source: 'DAYTONA_API', traceId: 't-1', retryAfterSeconds: 3 })
    expect(error.cause).toBe(cause)
    expect(error.name).toBe('ConflictError')
    const bare = new v2.BadRequestError('bad')
    expect([bare.statusCode, bare.code, bare.source, bare.traceId]).toEqual([undefined, undefined, undefined, undefined])
  })

  it('gives TransferCancelledError the same defaults as the Python SDK', () => {
    const error = new v2.TransferCancelledError()
    expect([error.message, error.statusCode, error.code, error.source]).toEqual(['file transfer cancelled', 499, 'REQUEST_CANCELLED', 'SDK'])
    const custom = new v2.TransferCancelledError('upload cancelled', { source: 'CALLER' })
    expect([custom.message, custom.statusCode, custom.code, custom.source]).toEqual(['upload cancelled', 499, 'REQUEST_CANCELLED', 'CALLER'])
  })

  it('defaults LimitExceededError.code', () => {
    expect(new v2.LimitExceededError('limit reached').code).toBe('LIMIT_EXCEEDED')
    expect(new v2.LimitExceededError('limit reached', { limit: 'max_concurrent_sandboxes', current: 3, max: 3 }).max).toBe(3)
  })

  it('keeps ImageBuildFailedError.buildError separate from the message', () => {
    const bare = new v2.ImageBuildFailedError('image img_1 build failed', { imageId: 'img_1' })
    expect(bare.message).toBe('image img_1 build failed')
    expect(bare.buildError).toBeUndefined()
    const recorded = new v2.ImageBuildFailedError('image img_1 build failed', { imageId: 'img_1', buildError: 'RUN exited 1' })
    expect(recorded.buildError).toBe('RUN exited 1')
    expect(recorded.message).toBe('image img_1 build failed')
    const fromWire = errorFromEnvelopeBody(JSON.stringify({ code: 'IMAGE_BUILD_FAILED', source: 'DAYTONA_API', message: 'failed' }), 'application/json', 409, 'x')
    expect(fromWire).toBeInstanceOf(v2.ImageBuildFailedError)
    expect((fromWire as v2.ImageBuildFailedError).buildError).toBeUndefined()
  })

  it('keeps an unknown code verbatim with the status category', () => {
    const error = errorFromEnvelopeBody(JSON.stringify({ code: 'SOMETHING_NEW', source: 'DAYTONA_SOMETHING', message: 'x' }), 'application/json', 404, 'x')
    expect(error.constructor).toBe(v2.NotFoundError)
    expect([error.code, error.source]).toEqual(['SOMETHING_NEW', 'DAYTONA_SOMETHING'])
    expect(v2.codes.isKnownErrorCode('SOMETHING_NEW')).toBe(false)
  })
})

describe('codes namespace', () => {
  it('has the same member names as the Python SDK', () => {
    expect(Object.keys(v2.codes).sort()).toEqual([
      'API_CODE_EXTRAS',
      'API_ERROR_CODES',
      'CODE_STATUS',
      'DAEMON_CODE_EXTRAS',
      'DAEMON_ERROR_CODES',
      'GENERIC_ERROR_CODES',
      'PROXY_CODE_EXTRAS',
      'PROXY_ERROR_CODES',
      'ROUTER_CODE_EXTRAS',
      'ROUTER_ERROR_CODES',
      'SDK_ERROR_CODES',
      'isKnownErrorCode',
    ])
  })

  it('replaces the flat catalog exports', () => {
    // Every member the codes namespace exposes, so a catalog array or extras
    // table re-exported at the top level fails here.
    const members = Object.keys(v2.codes)
    expect(members.length).toBeGreaterThan(0)
    for (const name of members) expect(Object.keys(v2)).not.toContain(name)
    expect(Object.keys(v2)).toContain('codes')
  })

  it('holds frozen data', () => {
    for (const name of ['API_ERROR_CODES', 'DAEMON_ERROR_CODES', 'GENERIC_ERROR_CODES', 'PROXY_ERROR_CODES', 'ROUTER_ERROR_CODES', 'CODE_STATUS', 'API_CODE_EXTRAS', 'DAEMON_CODE_EXTRAS', 'PROXY_CODE_EXTRAS', 'ROUTER_CODE_EXTRAS'] as const) {
      expect(Object.isFrozen(v2.codes[name])).toBe(true)
    }
    for (const fields of Object.values(v2.codes.API_CODE_EXTRAS)) expect(Object.isFrozen(fields)).toBe(true)
    expect(() => {
      ;(v2.codes.CODE_STATUS as Record<string, number>).NOT_FOUND = 500
    }).toThrow(TypeError)
    expect(v2.codes.CODE_STATUS.NOT_FOUND).toBe(404)
    expect(v2.codes.isKnownErrorCode('PROCESS_NOT_FOUND')).toBe(true)
    expect(v2.codes.isKnownErrorCode(undefined)).toBe(false)
  })
})

describe('errors the SDK raises itself', () => {
  it('carry source SDK and the catalog status, code and class', async () => {
    const transport = new HttpTransport('http://toolbox.invalid')
    const git = new GitService(INTERNAL, transport)
    await expect(git.commit('/repo', 'msg', { author: '', email: '' })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    await expect(git.commit('/repo', 'msg', { author: '', email: '' })).rejects.toBeInstanceOf(v2.BadRequestError)
    const processes = new ProcessService(INTERNAL, transport, 'sbx-1')
    const foreign = () => processes.connect({ sandboxId: 'sbx-2', processId: 'p', kind: 'exec' } as never)
    expect(foreign).toThrow(v2.BadRequestError)
    expect(foreign).toThrow(expect.objectContaining({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' }))
  })
})

describe('error constructors refuse a value that is not an options object', () => {
  const constructors: ReadonlyArray<readonly [string, new (message: string, options?: never) => Error]> = [
    ['DaytonaError', v2.DaytonaError as never],
    ['NotFoundError', v2.NotFoundError as never],
    ['LimitExceededError', v2.LimitExceededError as never],
    ['ConnectionError', v2.ConnectionError as never],
    ['ClientTimeoutError', v2.ClientTimeoutError as never],
    ['TransferCancelledError', v2.TransferCancelledError as never],
    ['ImageBuildFailedError', v2.ImageBuildFailedError as never],
    ['BuildLogInterruptedError', v2.BuildLogInterruptedError as never],
  ]

  it.each(constructors)('%s refuses a positional status', (_name, ErrorClass) => {
    // Plain JavaScript: `new NotFoundError('x', 404)` would otherwise drop the 404.
    for (const [value, given] of [[404, 'a number'], [null, 'null'], ['x', 'a string'], [[404], 'an array'], [new Map(), 'a built-in Map']] as const) {
      let thrown: unknown
      try {
        new ErrorClass('x', value as never)
      } catch (error) {
        thrown = error
      }
      expect(thrown).toBeInstanceOf(v2.BadRequestError)
      expect(thrown).toMatchObject({ message: `error options must be an options object, got ${given}`, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
    }
    expect(new ErrorClass('x')).toBeInstanceOf(v2.DaytonaError)
    expect(new ErrorClass('x', undefined)).toBeInstanceOf(v2.DaytonaError)
    expect(new ErrorClass('x', { statusCode: 418 } as never)).toMatchObject({ statusCode: 418 })
  })

  it.each(constructors)('%s refuses an enumerable symbol key, own or inherited', (_name, ErrorClass) => {
    const key = Symbol('extra')
    const own = { statusCode: 418, [key]: true }
    const inherited = Object.create({ [key]: true }) as object
    for (const options of [own, inherited]) {
      expect(() => new ErrorClass('x', options as never)).toThrow(new TypeError('ErrorOptions has no option `Symbol(extra)`'))
    }
    // A non-enumerable symbol (a class's own bookkeeping) is not an option.
    const hidden = { statusCode: 418 }
    Object.defineProperty(hidden, key, { value: true, enumerable: false })
    expect(new ErrorClass('x', hidden as never)).toMatchObject({ statusCode: 418 })
  })
})

describe('error constructor options', () => {
  it('keep the fields a prototype-based options object inherits', () => {
    const inherited = Object.create({ statusCode: 418, code: 'TEAPOT', source: 'SDK' }) as v2.ErrorOptions
    expect(new v2.DaytonaError('x', inherited)).toMatchObject({ statusCode: 418, code: 'TEAPOT', source: 'SDK' })
    expect(new v2.ImageBuildFailedError('x', Object.create({ statusCode: 418, imageId: 'img' }) as never)).toMatchObject({ statusCode: 418, imageId: 'img' })
    expect(new v2.ClientTimeoutError('x', Object.create({ statusCode: 504 }) as never)).toMatchObject({ statusCode: 504, code: 'REQUEST_TIMEOUT' })
    expect(new v2.BuildLogInterruptedError('x', Object.create({ cursor: 'c-1' }) as never)).toMatchObject({ cursor: 'c-1', code: 'BUILD_LOG_INTERRUPTED' })
  })

  it('documents the optional arguments of the specialised constructors as optional', () => {
    expect(new v2.DockerWaitTimeoutError()).toMatchObject({ statusCode: 408, message: 'docker did not become ready within the requested timeout' })
    expect(new v2.SandboxTerminalStateError('sbx', 'destroyed')).toMatchObject({ state: 'destroyed', statusCode: 409 })
  })

  it('SandboxTerminalStateError takes rawState as an option, like Python raw_state=', () => {
    const error = new v2.SandboxTerminalStateError('sbx', 'destroyed', { rawState: 'deleted' })
    expect(error).toMatchObject({ state: 'destroyed', rawState: 'deleted', statusCode: 409, code: 'CONFLICT', source: 'SDK' })
    expect(new v2.SandboxTerminalStateError('sbx', 'destroyed').rawState).toBeUndefined()
    // The server's explanation is an option too; the message ends with it (Python reason=).
    expect(new v2.SandboxTerminalStateError('sbx', 'error', { reason: 'volume detach failed' })).toMatchObject({
      reason: 'volume detach failed',
      message: 'sandbox sbx reached terminal state error: volume detach failed',
    })
    expect(new v2.SandboxTerminalStateError('sbx', 'error', { reason: '' }).reason).toBeUndefined()
    // The former positional form is refused rather than dropped.
    expect(() => new v2.SandboxTerminalStateError('sbx', 'destroyed', 'deleted' as never)).toThrow(
      expect.objectContaining({ constructor: v2.BadRequestError, message: 'error options must be an options object, got a string' }),
    )
  })
})
