/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { HttpTransport } from '../src/v2/http'
import {
  BadRequestError, ProcessHandle, ProcessService, Session, ProcessNotFoundError, SessionEndedError, SessionNotFoundError,
} from '../src/v2/index'
import type { CreateSessionOptions, SessionRunOptions, SessionStartOptions, SessionJSON } from '../src/v2/index'
import { WAIT_READ_SLACK_MS } from '../src/v2/Process'

const terminal = {
  id: 'command-1', kind: 'exec', state: 'terminal', reason: 'exited', exitCode: 0,
  logs: { eof: true, frames: [
    { channel: 'stdout', data: 'hello', cursor: '1' },
    { channel: 'stderr', data: 'warning', cursor: '2' },
    { channel: 'setup', data: 'hidden', cursor: '3' },
  ] },
} as const

describe('shell sessions', () => {
  afterEach(() => jest.restoreAllMocks())

  it('posts shell settings when creating a session', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ id: 'shell-1', kind: 'session' })
    const process = new ProcessService(transport, 'sandbox-1')
    const options: CreateSessionOptions = { cwd: '/app', env: { CI: '1' }, shell: 'bash', login: true, name: 'build' }
    // When
    const session = await process.createSession(options)
    // Then
    expect(post).toHaveBeenCalledWith('/processes', { kind: 'session', ...options })
    expect(session).toBeInstanceOf(Session)
    expect(session.toJSON()).toEqual({ sandboxId: 'sandbox-1', sessionId: 'shell-1' })
  })

  it('posts only the kind when creation options are omitted', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ id: 'shell-1' })
    // When
    await new ProcessService(transport, 'sandbox-1').createSession()
    // Then
    expect(post).toHaveBeenCalledWith('/processes', { kind: 'session' })
  })

  it.each<string | SessionJSON>(['shell-1', { sandboxId: 'sandbox-1', sessionId: 'shell-1' }])(
    'reconnects without a request when given %j', (identity) => {
      // Given
      const transport = new HttpTransport('http://toolbox.invalid')
      const request = jest.spyOn(transport.http, 'request')
      // When
      const session = new ProcessService(transport, 'sandbox-1').connectSession(identity)
      // Then
      expect(session.id).toBe('shell-1')
      expect(session.sandboxId).toBe('sandbox-1')
      expect(session.toJSON()).toEqual({ sandboxId: 'sandbox-1', sessionId: 'shell-1' })
      expect(request).not.toHaveBeenCalled()
    },
  )

  it('rejects a serialized session when it belongs to another sandbox', () => {
    // Given
    const process = new ProcessService(new HttpTransport('http://toolbox.invalid'), 'sandbox-1')
    // When / Then
    expect(() => process.connectSession({ sandboxId: 'sandbox-2', sessionId: 'shell-1' }))
      .toThrow(expect.objectContaining({ code: 'BAD_REQUEST', source: 'SDK' }))
    expect(() => process.connectSession({ sandboxId: 'sandbox-2', sessionId: 'shell-1' })).toThrow(BadRequestError)
  })

  it.each([
    ['a missing sessionId', { sandboxId: 'sandbox-1' }],
    ['a null sessionId', { sandboxId: 'sandbox-1', sessionId: null }],
    ['a numeric sessionId', { sandboxId: 'sandbox-1', sessionId: 7 }],
    ['an empty sessionId', { sandboxId: 'sandbox-1', sessionId: '' }],
    ['a missing sandboxId', { sessionId: 'shell-1' }],
  ])('refuses %s before any request, so a command never runs outside its shell', (_label, identity) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post')
    const process = new ProcessService(transport, 'sandbox-1')
    // When / Then
    expect(() => process.connectSession(identity as unknown as SessionJSON)).toThrow(BadRequestError)
    expect(() => new Session(process, '' as string)).toThrow(BadRequestError)
    expect(post).not.toHaveBeenCalled()
  })

  it('forwards executionTimeout with sessionId when running a command in one round trip', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue(terminal)
    const get = jest.spyOn(transport, 'get')
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    const options: SessionRunOptions = { stdin: 'open', timeout: 100, executionTimeout: 200.1, name: 'job', keepLogs: 'until_cleanup' }
    // When
    const result = await session.run('echo hello', options)
    // Then
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/processes?waitMs=100&logs=text', {
      sessionId: 'shell-1', shellCommand: 'echo hello', stdin: 'open', timeoutMs: 201, name: 'job', keepLogs: 'until_cleanup',
    }, { timeout: 100 + WAIT_READ_SLACK_MS })
    expect(get).not.toHaveBeenCalled()
    expect(result).toMatchObject({ processId: 'command-1', reason: 'exited', exitCode: 0, stdout: 'hello', stderr: 'warning', output: 'hellowarning' })
  })

  it.each([undefined, 'none', 'on_exit_ttl'] as const)('retains run output when keepLogs is %s', async (keepLogs) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue(terminal)
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    // When
    await session.run('true', { keepLogs })
    // Then
    expect(post).toHaveBeenCalledWith('/processes?waitMs=0&logs=text', {
      sessionId: 'shell-1', shellCommand: 'true', keepLogs: 'on_exit_ttl',
    }, { timeout: 0 })
  })

  it('uses process wait and logs when the inline response is still running', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'post').mockResolvedValue({ id: 'command-1', kind: 'exec', state: 'running' })
    const get = jest.spyOn(transport, 'get')
      .mockResolvedValueOnce({ reason: 'exited', exitCode: 7 })
      .mockResolvedValueOnce({ frames: [{ channel: 'stdout', data: 'later', cursor: '1' }] })
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    // When
    const result = await session.run('echo later; exit 7')
    // Then
    expect(result).toMatchObject({ reason: 'exited', exitCode: 7, stdout: 'later' })
    expect(get.mock.calls.map(([path]) => path)).toEqual(['/processes/command-1/wait?timeoutMs=0', '/processes/command-1/logs'])
  })

  it('forwards all command options when starting a session command', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ id: 'command-1', kind: 'exec' })
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    const options: SessionStartOptions = { stdin: 'open', executionTimeout: 1200, name: 'serve', keepLogs: 'none' }
    // When
    const handle = await session.start('npm run dev', options)
    // Then
    expect(post).toHaveBeenCalledWith('/processes', {
      sessionId: 'shell-1', shellCommand: 'npm run dev', stdin: 'open', timeoutMs: 1200, name: 'serve', keepLogs: 'none',
    }, undefined)
    expect(handle).toBeInstanceOf(ProcessHandle)
    expect(handle.toJSON()).toEqual({ sandboxId: 'sandbox-1', processId: 'command-1', kind: 'exec' })
  })

  it('uses only the bound identity when start options are omitted', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockResolvedValue({ id: 'command-1' })
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    // When
    await session.start('true')
    // Then
    expect(post).toHaveBeenCalledWith('/processes', { sessionId: 'shell-1', shellCommand: 'true' }, undefined)
  })

  it('returns the live cwd when getting the encoded session process', async () => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const get = jest.spyOn(transport, 'get').mockResolvedValue({ id: 'shell/!', kind: 'session', state: 'running', cwd: '/new' })
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell/!')
    // When
    const info = await session.get()
    // Then
    expect(get).toHaveBeenCalledWith('/processes/shell%2F%21')
    expect(info).toMatchObject({ kind: 'session', rawKind: 'session', cwd: '/new' })
  })

  it.each(['delete', Symbol.asyncDispose] as const)('deletes the session when calling %s', async (method) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const remove = jest.spyOn(transport, 'delete').mockResolvedValue(undefined)
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell/!')
    // When
    await session[method]()
    // Then
    expect(remove).toHaveBeenCalledTimes(1)
    expect(remove).toHaveBeenCalledWith('/processes/shell%2F%21')
  })

  it('scope exit tolerates a session already deleted in the scope', async () => {
    const transport = new HttpTransport('http://toolbox.invalid')
    jest.spyOn(transport, 'delete')
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new ProcessNotFoundError('gone', { statusCode: 404, code: 'PROCESS_NOT_FOUND', source: 'DAYTONA_DAEMON' }))
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell/!')
    await session.delete()
    await expect(session[Symbol.asyncDispose]()).resolves.toBeUndefined()
  })

  it.each([
    new SessionNotFoundError('missing', { statusCode: 404, code: 'SESSION_NOT_FOUND', source: 'DAYTONA_DAEMON' }),
    new SessionEndedError('ended', { statusCode: 410, code: 'SESSION_ENDED', source: 'DAYTONA_DAEMON' }),
  ])('surfaces %s without recreating a session', async (error) => {
    // Given
    const transport = new HttpTransport('http://toolbox.invalid')
    const post = jest.spyOn(transport, 'post').mockRejectedValue(error)
    const session = new ProcessService(transport, 'sandbox-1').connectSession('shell-1')
    // When / Then
    await expect(session.run('true')).rejects.toBe(error)
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0]?.[1]).toMatchObject({ sessionId: 'shell-1', shellCommand: 'true' })
  })
})
