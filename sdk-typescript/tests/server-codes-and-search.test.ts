/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Image, file, interpreter and git failures map to their exported classes;
// `git.deleteBranch({ force })`; `fs.grep()` / `fs.glob()` report the paths
// they skipped. Responses use the server's exact shapes, served over real
// HTTP. The Python twin is tests/test_server_codes_and_search.py.

import { createServer } from 'node:http'
import type { IncomingMessage, Server } from 'node:http'
import { once } from 'node:events'
import * as v2 from '../src/v2/index'
import { createDaytonaError } from '../src/v2/errors/DaytonaError'
import { FileSystemService } from '../src/v2/FileSystem'
import { GitService } from '../src/v2/Git'
import { HttpTransport } from '../src/v2/http'
import { InterpreterService } from '../src/v2/Interpreter'
import { INTERNAL } from '../src/v2/internal'

type ErrorClass = abstract new (...args: never[]) => v2.DaytonaError

const API = 'DAYTONA_API'
const DAEMON = 'DAYTONA_DAEMON'

const SERVER_ERRORS: ReadonlyArray<readonly [number, string, string, string, ErrorClass, ErrorClass]> = [
  [409, 'IMAGE_NAME_TAKEN', API, "image name 'py312' already exists for different content or import parameters", v2.ImageNameTakenError, v2.ConflictError],
  [409, 'IMAGE_NAME_TAKEN', API, "'py312' is a machine snapshot in this project; images and snapshots share one name namespace, so pick another image name", v2.ImageNameTakenError, v2.ConflictError],
  [400, 'IMAGE_NAME_RESERVED', API, "image name 'img_x' is reserved: 'img_' is a resource id prefix", v2.ImageNameReservedError, v2.BadRequestError],
  [403, 'FILE_ACCESS_DENIED', DAEMON, '/root/x: permission denied', v2.FileAccessDeniedError, v2.ForbiddenError],
  [400, 'INVALID_FILE_PATH', DAEMON, 'path must be absolute', v2.InvalidFilePathError, v2.BadRequestError],
  [400, 'INVALID_FILE_PATH', DAEMON, '/dev/null: not a regular file', v2.InvalidFilePathError, v2.BadRequestError],
  [500, 'FILE_READ_FAILED', DAEMON, '/x: read failed', v2.FileReadFailedError, v2.InternalServerError],
  [400, 'UNSUPPORTED_OPERATION', DAEMON, 'unsupported language: ruby', v2.UnsupportedOperationError, v2.BadRequestError],
  [503, 'KERNEL_UNAVAILABLE', DAEMON, 'python kernel failed to start', v2.KernelUnavailableError, v2.ServiceUnavailableError],
  [401, 'GIT_AUTH_FAILED', DAEMON, 'git authentication failed', v2.GitAuthFailedError, v2.AuthenticationError],
  [404, 'GIT_REPO_NOT_FOUND', DAEMON, 'repository does not exist', v2.GitRepoNotFoundError, v2.NotFoundError],
  [404, 'GIT_BRANCH_NOT_FOUND', DAEMON, 'branch does not exist: x', v2.GitBranchNotFoundError, v2.NotFoundError],
  [409, 'GIT_BRANCH_EXISTS', DAEMON, 'branch already exists: x', v2.GitBranchExistsError, v2.ConflictError],
  [409, 'CONFLICT', DAEMON, "delete branch failed: error: the branch 'x' is not fully merged", v2.ConflictError, v2.ConflictError],
]

describe('server error codes', () => {
  it.each(SERVER_ERRORS)('%i %s maps to its exported class', (status, code, source, message, cls, category) => {
    const error = createDaytonaError(message, status, code, source)
    expect(error.constructor).toBe(cls)
    expect(error).toBeInstanceOf(category)
    expect(error).toMatchObject({ statusCode: status, code, source })
  })
})

interface Reply {
  readonly status: number
  readonly body?: unknown
}

interface Seen {
  readonly method: string
  readonly path: string
  readonly body: string
}

let server: Server
let base: string
let replies: Reply[]
let seen: Seen[]

async function bodyOf(request: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = []
  for await (const chunk of request) chunks.push(Buffer.from(chunk as Uint8Array))
  return Buffer.concat(chunks).toString('utf8')
}

beforeEach(async () => {
  replies = []
  seen = []
  server = createServer((request, response) => {
    void bodyOf(request).then((body) => {
      seen.push({ method: request.method ?? '', path: (request.url ?? '').split('?')[0] ?? '', body })
      const reply = replies.shift() ?? { status: 500, body: { code: 'INTERNAL_SERVER_ERROR', message: 'unexpected request', source: DAEMON } }
      if (reply.body === undefined) {
        response.writeHead(reply.status)
        response.end()
        return
      }
      response.writeHead(reply.status, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(reply.body))
    })
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (address === null || typeof address === 'string') throw new TypeError('expected TCP address')
  base = `http://127.0.0.1:${address.port}`
})

afterEach(async () => {
  server.close()
  await once(server, 'close')
})

const transport = (): HttpTransport => new HttpTransport(base, 'token')
const daemonError = (status: number, code: string, message: string): Reply => ({ status, body: { code, message, source: DAEMON } })

describe('git.deleteBranch', () => {
  it.each([
    [undefined, { path: '/repo', name: 'feature' }],
    [{}, { path: '/repo', name: 'feature' }],
    [{ force: false }, { path: '/repo', name: 'feature' }],
    [{ force: true }, { path: '/repo', name: 'feature', force: true }],
  ] as const)('with %p sends force only when true', async (options, body) => {
    replies.push({ status: 204 })
    await new GitService(INTERNAL, transport()).deleteBranch('/repo', 'feature', options)
    expect(seen.map(({ method, path }) => [method, path])).toEqual([['DELETE', '/git/branches']])
    expect(JSON.parse(seen[0]?.body ?? '')).toStrictEqual(body)
  })

  it.each([null, 1, 'true'])('refuses force %p before any request', async (force) => {
    const call = new GitService(INTERNAL, transport()).deleteBranch('/repo', 'feature', { force } as never)
    await expect(call).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: 'force must be a boolean' })
    expect(seen).toEqual([])
  })

  it.each([
    [daemonError(409, 'CONFLICT', "delete branch failed: error: the branch 'x' is not fully merged"), v2.ConflictError],
    [daemonError(404, 'GIT_BRANCH_NOT_FOUND', 'branch does not exist: x'), v2.GitBranchNotFoundError],
    [daemonError(404, 'GIT_REPO_NOT_FOUND', 'repository does not exist'), v2.GitRepoNotFoundError],
  ] as const)('a %p failure throws its class', async (reply, cls) => {
    replies.push(reply)
    const error: unknown = await new GitService(INTERNAL, transport()).deleteBranch('/repo', 'x').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(cls)
    expect((error as Error).constructor).toBe(cls)
  })
})

describe('git failures', () => {
  const calls: Record<string, (git: GitService) => Promise<unknown>> = {
    createBranch: (git) => git.createBranch('/repo', 'x'),
    checkoutBranch: (git) => git.checkoutBranch('/repo', 'x'),
    pull: (git) => git.pull('/repo'),
    clone: (git) => git.clone('https://example.com/missing.git', { path: '/repo' }),
  }
  it.each([
    ['createBranch', daemonError(409, 'GIT_BRANCH_EXISTS', 'branch already exists: x'), v2.GitBranchExistsError],
    ['checkoutBranch', daemonError(404, 'GIT_BRANCH_NOT_FOUND', 'checkout failed: x'), v2.GitBranchNotFoundError],
    ['checkoutBranch', daemonError(409, 'CONFLICT', 'checkout failed: local changes'), v2.ConflictError],
    ['pull', daemonError(401, 'GIT_AUTH_FAILED', 'git authentication failed'), v2.GitAuthFailedError],
    ['clone', daemonError(404, 'GIT_REPO_NOT_FOUND', 'repository does not exist'), v2.GitRepoNotFoundError],
  ] as const)('%s throws its class', async (operation, reply, cls) => {
    replies.push(reply)
    const error: unknown = await calls[operation]?.(new GitService(INTERNAL, transport())).catch((caught: unknown) => caught)
    expect((error as Error).constructor).toBe(cls)
  })
})

describe('interpreter languages', () => {
  it.each([
    [daemonError(400, 'UNSUPPORTED_OPERATION', 'unsupported language: Python'), v2.UnsupportedOperationError],
    [daemonError(503, 'KERNEL_UNAVAILABLE', 'python kernel failed to start'), v2.KernelUnavailableError],
  ] as const)('%p throws its class', async (reply, cls) => {
    replies.push(reply)
    const interpreter = new InterpreterService(INTERNAL, transport(), 'sbx')
    const error: unknown = await interpreter.createContext({ language: 'Python' }).catch((caught: unknown) => caught)
    expect((error as Error).constructor).toBe(cls)
  })
})

const MATCH = { file: '/w/a.txt', line: 3, content: 'needle' }

describe('fs.grep', () => {
  it.each([
    [{ items: [MATCH], skipped: ['/w/private', '/w/secret.txt'] }, ['/w/private', '/w/secret.txt']],
    [{ items: [MATCH], skipped: [] }, []],
    [{ items: [MATCH] }, []],
    [[MATCH], []],
  ] as const)('reads %j', async (body, skipped) => {
    replies.push({ status: 200, body })
    const result: v2.FileSearchResult<v2.FileMatch> = await new FileSystemService(INTERNAL, transport()).grep('/w', 'needle')
    expect(seen[0]?.path).toBe('/files/find')
    expect(result).toStrictEqual({ items: [MATCH], skipped })
    expect(Object.isFrozen(result)).toBe(true)
    expect(Object.isFrozen(result.skipped)).toBe(true)
  })
})

describe('fs.glob', () => {
  it.each([
    [{ files: ['/w/a.py'], skipped: ['/w/private'] }, ['/w/private']],
    [{ files: ['/w/a.py'], skipped: [] }, []],
    [{ files: ['/w/a.py'] }, []],
  ] as const)('reads %j', async (body, skipped) => {
    replies.push({ status: 200, body })
    const result: v2.FileSearchResult<string> = await new FileSystemService(INTERNAL, transport()).glob('/w', '*.py')
    expect(seen[0]?.path).toBe('/files/search')
    expect(result).toStrictEqual({ items: ['/w/a.py'], skipped })
    expect(Object.isFrozen(result.skipped)).toBe(true)
  })

  it('throws FileAccessDeniedError for an unreadable root', async () => {
    replies.push(daemonError(403, 'FILE_ACCESS_DENIED', '/root: permission denied'))
    await expect(new FileSystemService(INTERNAL, transport()).glob('/root', '*')).rejects.toBeInstanceOf(v2.FileAccessDeniedError)
  })
})

describe('search responses that break the contract', () => {
  it.each([
    ['grep', { items: [], skipped: '/w/private' }, /grep response: skipped/],
    ['grep', { items: [], skipped: [3] }, /grep response: skipped/],
    ['grep', { skipped: [] }, /grep response: items/],
    ['glob', { files: [], skipped: { path: '/w' } }, /glob response: skipped/],
    ['glob', { files: [], skipped: [null] }, /glob response: skipped/],
  ] as const)('%s refuses %j', async (operation, body, message) => {
    replies.push({ status: 200, body })
    const fs = new FileSystemService(INTERNAL, transport())
    const call = operation === 'grep' ? fs.grep('/w', 'x') : fs.glob('/w', '*')
    await expect(call).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: expect.stringMatching(message) })
  })
})
