// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import { AxiosError, AxiosHeaders } from 'axios'

import {
  createAxiosDaytonaError,
  createDaytonaError,
  DaytonaA11yUnavailableError,
  DaytonaAuthenticationError,
  DaytonaAuthorizationError,
  DaytonaBadGatewayError,
  DaytonaBadRequestError,
  DaytonaConflictError,
  DaytonaConnectionError,
  DaytonaConnectionTimeoutError,
  DaytonaError,
  DaytonaFileNotFoundError,
  DaytonaFileReadFailedError,
  DaytonaGitAuthFailedError,
  DaytonaGitRemoteRejectedError,
  DaytonaGitTransportFailedError,
  DaytonaGoneError,
  DaytonaInternalServerError,
  DaytonaCursorExpiredError,
  DaytonaNameConflictError,
  DaytonaInvalidArgumentError,
  DaytonaInvalidFilePathError,
  DaytonaNotFoundError,
  DaytonaProcessExecutionTimeoutError,
  DaytonaRateLimitError,
  DaytonaServiceUnavailableError,
  DaytonaSessionEndedError,
  DaytonaTimeoutError,
  DaytonaUnprocessableEntityError,
  DaytonaValidationError,
  errorClassFromStatusCode,
} from '../errors/DaytonaError'

describe('DaytonaError construction', () => {
  it('constructs DaytonaError with properties', () => {
    const err = new DaytonaError('boom', 500, undefined, 'INTERNAL', 'DAYTONA_RUNNER')
    expect(err).toBeInstanceOf(Error)
    expect(err.name).toBe('DaytonaError')
    expect(err.message).toBe('boom')
    expect(err.statusCode).toBe(500)
    expect(err.code).toBe('INTERNAL')
    expect(err.source).toBe('DAYTONA_RUNNER')
  })

  it('exposes code through the deprecated errorCode alias', () => {
    const err = new DaytonaError('boom', 404, undefined, 'FILE_NOT_FOUND', 'DAYTONA_DAEMON')
    expect(err.errorCode).toBe('FILE_NOT_FOUND')
    expect(new DaytonaError('plain').errorCode).toBeUndefined()
  })

  test.each([
    [DaytonaNotFoundError, 'DaytonaNotFoundError'],
    [DaytonaRateLimitError, 'DaytonaRateLimitError'],
    [DaytonaTimeoutError, 'DaytonaTimeoutError'],
  ])('constructs %s', (ErrCtor, expectedName) => {
    const err = new ErrCtor('x', 404)
    expect(err).toBeInstanceOf(DaytonaError)
    expect(err.name).toBe(expectedName)
    expect(err.statusCode).toBe(404)
  })

  it('preserves deprecated alias class names', () => {
    expect(new DaytonaValidationError('bad request').name).toBe('DaytonaValidationError')
    expect(new DaytonaAuthorizationError('forbidden').name).toBe('DaytonaAuthorizationError')
  })
})

describe('DaytonaInvalidArgumentError (client-side validation)', () => {
  it('carries no response metadata', () => {
    const err = new DaytonaInvalidArgumentError('Timeout must be a non-negative number')
    expect(err.name).toBe('DaytonaInvalidArgumentError')
    expect(err.message).toBe('Timeout must be a non-negative number')
    expect(err.statusCode).toBeUndefined()
    expect(err.code).toBeUndefined()
    expect(err.source).toBeUndefined()
  })

  it('still matches legacy validation catches', () => {
    const err = new DaytonaInvalidArgumentError('bad arg')
    expect(err).toBeInstanceOf(DaytonaValidationError)
    expect(err).toBeInstanceOf(DaytonaBadRequestError)
    expect(err).toBeInstanceOf(DaytonaError)
  })

  it('is distinct from server-returned 400 and 422 errors', () => {
    const local = new DaytonaInvalidArgumentError('bad arg')
    const badRequest = createDaytonaError('server rejected', 400)
    const unprocessable = createDaytonaError('semantically invalid', 422)

    expect(badRequest).not.toBeInstanceOf(DaytonaInvalidArgumentError)
    expect(unprocessable).not.toBeInstanceOf(DaytonaInvalidArgumentError)
    expect(local).not.toBeInstanceOf(DaytonaUnprocessableEntityError)
    expect(badRequest.statusCode).toBe(400)
    expect(local.statusCode).toBeUndefined()
  })

  it('is not produced by status-code classification', () => {
    expect(errorClassFromStatusCode(400)).not.toBe(DaytonaInvalidArgumentError)
  })
})

describe('DaytonaConnectionTimeoutError legacy timeout compatibility', () => {
  it('matches DaytonaTimeoutError as well as DaytonaConnectionError', () => {
    const err = new DaytonaConnectionTimeoutError('Operation timed out')
    expect(err).toBeInstanceOf(DaytonaTimeoutError)
    expect(err).toBeInstanceOf(DaytonaConnectionError)
    expect(err).toBeInstanceOf(DaytonaError)
  })

  it('does not make plain connection errors look like timeouts', () => {
    expect(new DaytonaConnectionError('ECONNREFUSED')).not.toBeInstanceOf(DaytonaTimeoutError)
  })

  it('does not leak the widened match into DaytonaTimeoutError subclasses', () => {
    const err = new DaytonaConnectionTimeoutError('Operation timed out')
    expect(err).not.toBeInstanceOf(DaytonaProcessExecutionTimeoutError)
  })

  it('leaves ordinary timeout classification untouched', () => {
    const err = createDaytonaError('gateway timed out', 504)
    expect(err).toBeInstanceOf(DaytonaTimeoutError)
    expect(err).not.toBeInstanceOf(DaytonaConnectionError)

    const processTimeout = createDaytonaError('too slow', 408, undefined, 'PROCESS_EXECUTION_TIMEOUT', 'DAYTONA_DAEMON')
    expect(processTimeout).toBeInstanceOf(DaytonaProcessExecutionTimeoutError)
    expect(processTimeout).toBeInstanceOf(DaytonaTimeoutError)
  })
})

describe('HTTP status code classification', () => {
  test.each([
    [400, DaytonaValidationError],
    [401, DaytonaAuthenticationError],
    [403, DaytonaAuthorizationError],
    [404, DaytonaNotFoundError],
    [408, DaytonaTimeoutError],
    [409, DaytonaConflictError],
    [410, DaytonaGoneError],
    [422, DaytonaUnprocessableEntityError],
    [429, DaytonaRateLimitError],
    [500, DaytonaInternalServerError],
    [502, DaytonaBadGatewayError],
    [503, DaytonaServiceUnavailableError],
    [504, DaytonaTimeoutError],
  ])('maps status %s to its typed class', (statusCode, ErrCtor) => {
    expect(errorClassFromStatusCode(statusCode)).toBe(ErrCtor)
  })

  it('falls back to DaytonaError for unknown status codes', () => {
    expect(errorClassFromStatusCode(418)).toBe(DaytonaError)
    expect(errorClassFromStatusCode(undefined)).toBe(DaytonaError)
  })
})

describe('Domain code classification with status-class inheritance', () => {
  it('daemon GIT_AUTH_FAILED inherits from DaytonaAuthenticationError', () => {
    const err = createDaytonaError('git auth bad', 401, undefined, 'GIT_AUTH_FAILED', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaGitAuthFailedError)
    expect(err).toBeInstanceOf(DaytonaAuthenticationError)
  })

  it('daemon FILE_NOT_FOUND inherits from DaytonaNotFoundError', () => {
    const err = createDaytonaError('missing', 404, undefined, 'FILE_NOT_FOUND', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaFileNotFoundError)
    expect(err).toBeInstanceOf(DaytonaNotFoundError)
    expect(err.code).toBe('FILE_NOT_FOUND')
  })

  it('daemon SESSION_ENDED inherits from DaytonaGoneError', () => {
    const err = createDaytonaError('session ended', 410, undefined, 'SESSION_ENDED', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaSessionEndedError)
    expect(err).toBeInstanceOf(DaytonaGoneError)
  })

  it('daemon A11Y_UNAVAILABLE inherits from DaytonaServiceUnavailableError', () => {
    const err = createDaytonaError('a11y bus down', 503, undefined, 'A11Y_UNAVAILABLE', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaA11yUnavailableError)
    expect(err).toBeInstanceOf(DaytonaServiceUnavailableError)
  })

  it('daemon INVALID_FILE_PATH inherits from DaytonaBadRequestError', () => {
    const err = createDaytonaError('invalid file path: ..', 400, undefined, 'INVALID_FILE_PATH', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaInvalidFilePathError)
    expect(err).toBeInstanceOf(DaytonaBadRequestError)
    expect(err.code).toBe('INVALID_FILE_PATH')
  })

  it('daemon FILE_READ_FAILED inherits from DaytonaInternalServerError', () => {
    const err = createDaytonaError('failed to access file', 500, undefined, 'FILE_READ_FAILED', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaFileReadFailedError)
    expect(err).toBeInstanceOf(DaytonaInternalServerError)
    expect(err.code).toBe('FILE_READ_FAILED')
  })

  it('daemon GIT_TRANSPORT_FAILED inherits from DaytonaBadGatewayError', () => {
    const err = createDaytonaError('dns lookup failed', 502, undefined, 'GIT_TRANSPORT_FAILED', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaGitTransportFailedError)
    expect(err).toBeInstanceOf(DaytonaBadGatewayError)
    expect(err.code).toBe('GIT_TRANSPORT_FAILED')
  })

  it('daemon GIT_REMOTE_REJECTED inherits from DaytonaUnprocessableEntityError', () => {
    const err = createDaytonaError('pre-receive hook declined', 422, undefined, 'GIT_REMOTE_REJECTED', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaGitRemoteRejectedError)
    expect(err).toBeInstanceOf(DaytonaUnprocessableEntityError)
    expect(err.code).toBe('GIT_REMOTE_REJECTED')
  })

  it('daemon NAME_CONFLICT inherits from DaytonaConflictError', () => {
    const err = createDaytonaError('duplicate name', 409, undefined, 'NAME_CONFLICT', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaNameConflictError)
    expect(err).toBeInstanceOf(DaytonaConflictError)
  })

  it('falls back to status class when (source, code) is unknown', () => {
    const err = createDaytonaError('mystery 404', 404, undefined, 'UNKNOWN_CODE', 'DAYTONA_DAEMON')
    expect(err).toBeInstanceOf(DaytonaNotFoundError)
    expect(err).not.toBeInstanceOf(DaytonaFileNotFoundError)
  })

  it('code without source falls back to status class', () => {
    const err = createDaytonaError('no source', 401, undefined, 'GIT_AUTH_FAILED')
    expect(err).toBeInstanceOf(DaytonaAuthenticationError)
    expect(err).not.toBeInstanceOf(DaytonaGitAuthFailedError)
  })
})

describe('Axios error mapping', () => {
  it('classifies Axios timeouts as DaytonaConnectionTimeoutError', () => {
    const error = new AxiosError('timeout of 1000ms exceeded', 'ECONNABORTED', { timeout: 1000 } as never)

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaConnectionTimeoutError)
    expect(daytonaError).toBeInstanceOf(DaytonaConnectionError)
    expect(daytonaError.message).toBe(
      'HTTP request timed out after 1000ms waiting for a response. This is a client-side deadline' +
        ' (DaytonaConfig.requestTimeoutMs, or the per-call operation/execution timeout); any operation' +
        ' already started on the server may still be running.',
    )
  })

  it('omits the deadline value when the request config carries no timeout', () => {
    const error = new AxiosError('timeout of 0ms exceeded', 'ETIMEDOUT')

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaConnectionTimeoutError)
    expect(daytonaError.message).toContain('HTTP request timed out waiting for a response')
  })

  it('classifies network failures without a response as DaytonaConnectionError', () => {
    const error = new AxiosError('connect ECONNREFUSED', 'ERR_NETWORK', undefined, {} as never)

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaConnectionError)
    expect(daytonaError).not.toBeInstanceOf(DaytonaConnectionTimeoutError)
  })

  it('maps HTTP status + domain code to the precise subclass', () => {
    const headers = new AxiosHeaders({ 'x-request-id': 'req_123' })
    const error = new AxiosError('Request failed with status code 404', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers } as never,
      data: { message: 'missing file', code: 'FILE_NOT_FOUND', source: 'DAYTONA_DAEMON' },
      headers,
      status: 404,
      statusText: 'Not Found',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaFileNotFoundError)
    expect(daytonaError).toBeInstanceOf(DaytonaNotFoundError)
    expect(daytonaError.statusCode).toBe(404)
    expect(daytonaError.code).toBe('FILE_NOT_FOUND')
    expect(daytonaError.source).toBe('DAYTONA_DAEMON')
    expect(daytonaError.headers).toBe(headers)
  })

  it('falls back to status-code class when no domain code is present', () => {
    const error = new AxiosError('Not found', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers: new AxiosHeaders() } as never,
      data: { message: 'missing thing' },
      headers: new AxiosHeaders(),
      status: 404,
      statusText: 'Not Found',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaNotFoundError)
    expect(daytonaError).not.toBeInstanceOf(DaytonaFileNotFoundError)
  })

  it('stringifies object payloads when mapping axios errors', () => {
    const error = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers: new AxiosHeaders() } as never,
      data: { nested: { reason: 'bad request' } },
      headers: new AxiosHeaders(),
      status: 500,
      statusText: 'Server Error',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaInternalServerError)
    expect(daytonaError.message).toBe('{"nested":{"reason":"bad request"}}')
  })

  it('does not use the deprecated "error" field as a fallback code', () => {
    const error = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers: new AxiosHeaders() } as never,
      data: { message: 'missing file', error: 'Not Found' },
      headers: new AxiosHeaders(),
      status: 404,
      statusText: 'Not Found',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaNotFoundError)
    expect(daytonaError.code).toBeUndefined()
  })

  it('falls back to error_code when code is absent', () => {
    const error = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers: new AxiosHeaders() } as never,
      data: { message: 'missing file', error_code: 'FILE_NOT_FOUND', source: 'DAYTONA_DAEMON' },
      headers: new AxiosHeaders(),
      status: 404,
      statusText: 'Not Found',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaFileNotFoundError)
    expect(daytonaError.code).toBe('FILE_NOT_FOUND')
  })

  it('maps CURSOR_EXPIRED responses to the typed conflict error', () => {
    const error = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers: new AxiosHeaders() } as never,
      data: { statusCode: 409, message: 'cursor expired', code: 'CURSOR_EXPIRED', source: 'DAYTONA_DAEMON' },
      headers: new AxiosHeaders(),
      status: 409,
      statusText: 'Conflict',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaCursorExpiredError)
    expect(daytonaError.code).toBe('CURSOR_EXPIRED')
  })

  it('falls back to the legacy error field for the message', () => {
    const error = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers: new AxiosHeaders() } as never,
      data: { error: 'legacy not found' },
      headers: new AxiosHeaders(),
      status: 404,
      statusText: 'Not Found',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaNotFoundError)
    expect(daytonaError.message).toBe('legacy not found')
  })

  it('creates a generic DaytonaError for unknown non-network failures', () => {
    const error = new AxiosError('unknown failure')

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaError)
    expect(daytonaError).not.toBeInstanceOf(DaytonaConnectionError)
  })

  it('preserves DaytonaAuthorizationError mapping for 403 responses', () => {
    const error = new AxiosError('forbidden', 'ERR_BAD_REQUEST', undefined, {} as never, {
      config: { headers: new AxiosHeaders() } as never,
      data: { message: 'forbidden' },
      headers: new AxiosHeaders(),
      status: 403,
      statusText: 'Forbidden',
    })

    const daytonaError = createAxiosDaytonaError(error)

    expect(daytonaError).toBeInstanceOf(DaytonaAuthorizationError)
  })
})
