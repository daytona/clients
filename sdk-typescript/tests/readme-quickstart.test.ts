/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { join } from 'node:path'
import ts from 'typescript'

it('runs the README quickstart through registry readiness, named create, exec and completed deletion', async () => {
  // Given the exact public quickstart and an import which is initially pending.
  const root = join(__dirname, '..')
  const source = /```ts\n([\s\S]*?)```/.exec(readFileSync(join(root, 'README.md'), 'utf8'))?.[1]
  if (source === undefined) throw new Error('README has no TypeScript quickstart')
  const config = ts.readConfigFile(join(root, 'tsconfig.json'), ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
  const fixture = join(root, 'README.quickstart.ts')
  const host = ts.createCompilerHost(parsed.options)
  const getSourceFile = host.getSourceFile.bind(host)
  host.getSourceFile = (path, languageVersion) => path === fixture
    ? ts.createSourceFile(path, source, languageVersion, true)
    : getSourceFile(path, languageVersion)
  const program = ts.createProgram([fixture], { ...parsed.options, noEmit: true }, host)
  expect(ts.getPreEmitDiagnostics(program).map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n'))).toEqual([])
  const image = { id: 'img-pull', name: 'py312', region_ids: [], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', anonymous: false, class: 'linux', origin: 'registry', region_id: 'region-a', retention: 'permanent', format_version: 1, format_outdated: false, platform: false, default: false, deprecated: false }
  const responses = [
    { id: 'img-pull', status: 'pending' },
    { ...image, status: 'pending' },
    { ...image, status: 'pending' },
    { ...image, status: 'ready' },
    { id: 'sbx-quickstart', status: 'running' },
    { access_token: 'toolbox-jwt', token_type: 'Bearer', expires_at: '2099-01-01T00:00:00Z', operations: [] },
    {
      id: 'proc-quickstart', kind: 'exec', state: 'terminal', reason: 'exited', exitCode: 0,
      logs: { frames: [{ cursor: 'c1', channel: 'stdout', data: 'Python 3.12.9\n', timestamp: 1 }], eof: true },
    },
    {},
    { code: 'NOT_FOUND', message: 'deleted' },
  ]
  const requests: { readonly method: string | undefined; readonly path: string; readonly wait: string | null; readonly waitExact: string | null; readonly body: unknown }[] = []
  let base = ''
  const server = createServer((request, response) => {
    let body = ''
    request.setEncoding('utf8')
    request.on('data', (chunk: string) => { body += chunk })
    request.on('end', () => {
      const url = new URL(request.url ?? '/', base)
      requests.push({ method: request.method, path: url.pathname, wait: url.searchParams.get('wait'), waitExact: url.searchParams.get('wait_exact'), body: body ? JSON.parse(body) : undefined })
      const payload = responses.shift()
      const status = requests.length === 9 ? 404 : 200
      response.writeHead(payload === undefined ? 500 : status, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(requests.length === 5 ? { ...payload, toolbox_proxy_url: `${base}/sandbox-api` } : payload))
    })
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('missing HTTP server address')
  base = `http://127.0.0.1:${address.port}`
  const previousUrl = process.env.DAYTONA_V2_API_URL
  process.env.DAYTONA_V2_API_URL = base
  const output: unknown[][] = []
  try {
    // When transpiling and executing the extracted block with the real public package import.
    const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 } })
    const run = new Function('require', 'console', 'exports', `return (async () => {\n${compiled.outputText}\nawait daytona.close()\n})()`)
    await run(require, { log: (...values: unknown[]) => output.push(values) }, {})

    // Then only a ready image is used, and delete has observed disappearance before returning.
    expect(requests.map(({ method, path, wait, waitExact }) => ({ method, path, wait, ...(waitExact === null ? {} : { waitExact }) }))).toEqual([
      { method: 'POST', path: '/images', wait: null },
      { method: 'GET', path: '/images/img-pull', wait: null },
      { method: 'GET', path: '/images/img-pull', wait: 'ready' },
      { method: 'GET', path: '/images/img-pull', wait: 'ready' },
      { method: 'POST', path: '/sandboxes', wait: null },
      { method: 'POST', path: '/sandboxes/sbx-quickstart/access-token', wait: null },
      { method: 'POST', path: '/sandbox-api/sbx-quickstart/processes', wait: null },
      { method: 'DELETE', path: '/sandboxes/sbx-quickstart', wait: null },
      // delete() holds its long poll for `deleted` exactly.
      { method: 'GET', path: '/sandboxes/sbx-quickstart', wait: null, waitExact: 'deleted' },
    ])
    expect(requests[0]?.body).toMatchObject({ name: 'py312', source: { type: 'registry', ref: 'python:3.12' } })
    expect(requests[4]?.body).toMatchObject({ image: 'py312' })
    expect(requests[6]?.body).toMatchObject({ shellCommand: 'python -V' })
    expect(output).toEqual([[0, 'Python 3.12.9\n']])
    expect(responses).toHaveLength(0)
  } finally {
    if (previousUrl === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = previousUrl
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  }
}, 30_000)
