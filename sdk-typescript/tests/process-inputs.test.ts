/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Process, interpreter and git arguments are refused before any request, with
// messages that name the called method's own parameters (twin of Python's
// tests/test_process_inputs.py).

import { INTERNAL } from '../src/v2/internal'
import { HttpTransport } from '../src/v2/http'
import { BadRequestError, GitService, InterpreterService, ProcessService, PtyService, UnsupportedOperationError } from '../src/v2/index'
import type { KillOptions } from '../src/v2/types'

const PROCESS = {
  id: 'p', kind: 'exec', state: 'running', cwd: '/', stdin: 'closed', keepLogs: 'on_exit_ttl',
  createdAt: '2026-01-01T00:00:00Z', truncatedHead: false,
}

function services() {
  const transport = new HttpTransport('http://toolbox.invalid')
  const request = jest.spyOn(transport.http, 'request').mockRejectedValue(new Error('no request expected'))
  const process = new ProcessService(INTERNAL, transport, 'sbx')
  return {
    transport,
    request,
    process,
    pty: new PtyService(INTERNAL, process, transport),
    interpreter: new InterpreterService(INTERNAL, transport, 'sbx'),
    git: new GitService(INTERNAL, transport),
  }
}

async function refused(call: () => Promise<unknown>, message: string): Promise<void> {
  const error: unknown = await call().then(() => undefined, (caught: unknown) => caught)
  expect(error).toBeInstanceOf(BadRequestError)
  expect(error).toMatchObject({ message, statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
}

type Services = ReturnType<typeof services>

describe('process and interpreter inputs', () => {
  afterEach(() => jest.restoreAllMocks())

  it.each<[string, (s: Services) => Promise<unknown>, string]>([
    ['pty.start(123)', (s) => s.pty.start(123 as never), 'command must be a string'],
    ['process.run({ cmd: 5 })', (s) => s.process.run({ cmd: 5 } as never), 'command.cmd must be a string'],
    ['process.run({})', (s) => s.process.run({} as never), 'command takes exactly one of cmd or argv'],
    ['process.start({ cmd, argv })', (s) => s.process.start({ cmd: 'ls', argv: ['ls'] } as never), 'command takes exactly one of cmd or argv'],
    ['session.run(undefined)', (s) => s.process.connectSession('s').run(undefined as never), 'command must be a string'],
    ['session.start(5)', (s) => s.process.connectSession('s').start(5 as never), 'command must be a string'],
    ['interpreter.run(123)', (s) => s.interpreter.run(123 as never), 'code must be a string'],
    ['interpreter.run(undefined)', (s) => s.interpreter.run(undefined as never), 'code must be a string'],
    ['interpreter.run language ""', (s) => s.interpreter.run('1', { language: '' }), 'language must be a non-empty string'],
    ['interpreter.run language 5', (s) => s.interpreter.run('1', { language: 5 as never }), 'language must be a non-empty string'],
    ['isolated run language ""', (s) => s.interpreter.run('1', { language: '', isolated: true }), 'language must be a non-empty string'],
    ['createContext language ""', (s) => s.interpreter.createContext({ language: '' }), 'language must be a non-empty string'],
    ['context.run(5)', (s) => s.interpreter.connectContext('ctx', 'python').run(5 as never), 'code must be a string'],
  ])('refuses %s before any request', async (_name, call, message) => {
    const s = services()
    await refused(() => call(s), message)
    expect(s.request).not.toHaveBeenCalled()
  })

  it('still starts the default shell for a PTY without a command', async () => {
    const s = services()
    const post = jest.spyOn(s.transport, 'post').mockResolvedValue({ ...PROCESS, id: 'pty-1', kind: 'pty', stdin: 'open', keepLogs: 'until_cleanup' })
    const handle = await s.pty.start()
    expect(handle.kind).toBe('pty')
    expect(post.mock.calls[0][1]).not.toHaveProperty('shellCommand')
  })

  it('refuses a conflicting language on connectContext, as run does', () => {
    const { interpreter } = services()
    const identity = { sandboxId: 'sbx', contextId: 'ctx', language: 'python' }
    expect(() => interpreter.connectContext(identity, 'javascript')).toThrow("language 'javascript' does not match the context's language 'python'")
    expect(() => interpreter.connectContext(interpreter.connectContext(identity), 'javascript')).toThrow("does not match the context's language")
    expect(interpreter.connectContext(identity, 'python').language).toBe('python')
    expect(() => interpreter.connectContext(identity, '')).toThrow('interpreter context language must be a non-empty string')
    expect(() => interpreter.connectContext(null as never, 'python')).toThrow('interpreter context id must be a non-empty string')
  })

  it('names pty.delete when refusing another kind', async () => {
    const s = services()
    const get = jest.spyOn(s.transport, 'get').mockResolvedValue(PROCESS)
    const error: unknown = await s.pty.delete('p').then(() => undefined, (caught: unknown) => caught)
    expect(error).toBeInstanceOf(UnsupportedOperationError)
    expect(error).toMatchObject({ message: 'pty.delete() is only supported for pty handles; handle kind is exec' })
    expect(get).toHaveBeenCalledTimes(1)
  })
})

describe('kill signals', () => {
  afterEach(() => jest.restoreAllMocks())

  it.each<[KillOptions, string]>([
    [{ signal: '' as never }, "signal must be one of TERM, KILL, INT, HUP, QUIT, USR1, USR2 (a bare name, without the SIG prefix), got ''"],
    [{ signal: 'SIGTERM' as never }, "signal must be one of TERM, KILL, INT, HUP, QUIT, USR1, USR2 (a bare name, without the SIG prefix), got 'SIGTERM'"],
    [{ signal: 'STOP' as never }, "signal must be one of TERM, KILL, INT, HUP, QUIT, USR1, USR2 (a bare name, without the SIG prefix), got 'STOP'"],
    [{ escalateTo: 'KILL' }, 'escalateTo needs escalateAfterSeconds'],
    [{ escalateAfterSeconds: 1, escalateTo: 'SIGKILL' as never }, "escalateTo must be one of TERM, KILL, INT, HUP, QUIT, USR1, USR2 (a bare name, without the SIG prefix), got 'SIGKILL'"],
    [{ escalateAfterSeconds: 601 }, 'escalateAfterSeconds must be at most 600 seconds'],
    [{ escalateAfterSeconds: 600.0001 }, 'escalateAfterSeconds must be at most 600 seconds'],
    [{ escalateAfterSeconds: 1e300 }, 'escalateAfterSeconds must be at most 600 seconds'],
    [{ escalateAfterSeconds: -1 }, 'escalateAfterSeconds must be a non-negative finite number of seconds'],
    [{ escalateAfterSeconds: Number.NaN }, 'escalateAfterSeconds must be a non-negative finite number of seconds'],
  ])('refuses %j before any request', async (options, message) => {
    const s = services()
    await refused(() => s.process.connect('p').kill(options), message)
    expect(s.request).not.toHaveBeenCalled()
  })

  it('normalizes signal case and accepts the longest delay', async () => {
    const s = services()
    const post = jest.spyOn(s.transport, 'post').mockResolvedValue(undefined)
    await s.process.connect('p').kill({ signal: 'int' as never, escalateAfterSeconds: 600, escalateTo: 'kill' as never })
    await s.process.connect('p').kill({ signal: 'term' as never })
    expect(post.mock.calls.map((call) => call[1])).toEqual([
      { signal: 'SIGINT', escalateAfterMs: 600000, escalateTo: 'SIGKILL' },
      { signal: 'SIGTERM' },
    ])
  })
})

describe('git booleans', () => {
  afterEach(() => jest.restoreAllMocks())

  it.each<[string, (git: GitService) => Promise<unknown>, string]>([
    ['commit allowEmpty', (git) => git.commit('/r', 'm', { author: 'a', email: 'e', allowEmpty: 'true' as never }), 'allowEmpty must be a boolean'],
    ['init bare', (git) => git.init('/r', { bare: 'yes' as never }), 'bare must be a boolean'],
    ['push setUpstream', (git) => git.push('/r', { setUpstream: 'yes' as never }), 'setUpstream must be a boolean'],
    ['restore staged', (git) => git.restore('/r', ['f'], { staged: 'false' as never }), 'staged must be a boolean'],
    ['restore worktree', (git) => git.restore('/r', ['f'], { worktree: 0 as never }), 'worktree must be a boolean'],
    ['clone insecureSkipTls', (git) => git.clone('u', { path: '/r', insecureSkipTls: 'true' as never }), 'insecureSkipTls must be a boolean'],
    ['deleteBranch force', (git) => git.deleteBranch('/r', 'b', { force: 'true' as never }), 'force must be a boolean'],
  ])('refuses a non-boolean %s before any request', async (_name, call, message) => {
    const s = services()
    await refused(() => call(s.git), message)
    expect(s.request).not.toHaveBeenCalled()
  })

  it('sends the booleans it accepts', async () => {
    const s = services()
    const post = jest.spyOn(s.transport, 'post').mockResolvedValueOnce({ hash: 'abc' }).mockResolvedValue(undefined)
    await s.git.commit('/r', 'm', { author: 'a', email: 'e', allowEmpty: true })
    await s.git.init('/r', { bare: true })
    await s.git.push('/r', { setUpstream: true })
    expect(post.mock.calls[0][1]).toMatchObject({ allow_empty: true })
    expect(post.mock.calls[1][1]).toMatchObject({ bare: true })
    expect(post.mock.calls[2][1]).toMatchObject({ setUpstream: true })
  })
})
