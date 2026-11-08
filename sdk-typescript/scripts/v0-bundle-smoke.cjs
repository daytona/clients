/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Smoke-test the bundled v0 namespace from an INSTALLED @daytona/sdk tarball.
//
//   node v0-bundle-smoke.cjs [<pinned tag>]
//
// Copy it into a scratch project that installed the packed tarball and run it
// there (sdk-ci's `typescript-v0-bundle` job and the publish job), so
// `require` resolves the installed package, not this checkout. No live API: a local HTTP
// server stands in for the v0 API, records what the bundled client sends, and
// refuses it. Checks that
//   - v2-only use never loads the v0 SDK (a fresh child process, CJS and ESM);
//   - `Daytona().v0` is the bundled v0 `Daytona` itself, loaded synchronously on
//     first access and the same instance after, from CJS and ESM consumers;
//   - `@daytona/sdk/v0` does not export the client: `require` gets an
//     error naming the root form, and ESM sees no `Daytona` export;
//   - the bundle is the pinned release (when a tag is given);
//   - a v0 create with a per-call `target` sends it in the request body, and a
//     v0 snapshot create sends its per-call `regionId`;
//   - the root's shared errors catch v0's error classes.
'use strict'

const http = require('node:http')
const path = require('node:path')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')

// Before this process loads anything: a fresh process that uses only d.v2 must
// leave the v0 SDK unloaded, and a fresh one using d.v0 must get the real v0
// client synchronously — from a CommonJS and an ES module consumer.
function child(args) {
  const run = spawnSync(process.execPath, args, { encoding: 'utf8', cwd: process.cwd() })
  assert.equal(run.status, 0, `child failed: ${run.stderr}`)
  return run.stdout.trim()
}
const V0_LOADED = "Object.keys(require.cache).some((k) => k.includes('/dist/cjs/v0/'))"
assert.equal(
  child(['-e', `const { Daytona } = require('@daytona/sdk'); void new Daytona({ apiKey: 'k' }).v2; console.log(${V0_LOADED})`]),
  'false',
  'CJS v2-only use loaded the v0 SDK',
)
assert.equal(
  child(['--input-type=module', '-e', `import { createRequire } from 'node:module'; import { Daytona } from '@daytona/sdk'; const require = createRequire(process.cwd() + '/'); void new Daytona({ apiKey: 'k' }).v2; console.log(${V0_LOADED})`]),
  'false',
  'ESM v2-only use loaded the v0 SDK',
)
for (const [label, args] of [
  ['CJS', ['-e', "const { Daytona } = require('@daytona/sdk'); const V0 = require('@daytona/sdk/v0'); const d = new Daytona({ apiKey: 'k' }); const c = d.v0; console.log([c.constructor.name, c instanceof V0.RootOnlyClientV0, d.v0 === c, c.volume.constructor.name, d.v0.volume === c.volume].join(' '))"]],
  ['ESM', ['--input-type=module', '-e', "import { createRequire } from 'node:module'; import { Daytona } from '@daytona/sdk'; const V0 = createRequire(process.cwd() + '/')('@daytona/sdk/v0'); const d = new Daytona({ apiKey: 'k' }); const c = d.v0; console.log([c.constructor.name, c instanceof V0.RootOnlyClientV0, d.v0 === c, c.volume.constructor.name, d.v0.volume === c.volume].join(' '))"]],
]) {
  assert.equal(child(args), 'Daytona true true VolumeService true', `${label}: d.v0 is not the real, cached v0 client`)
}

const root = require('@daytona/sdk')
const v0 = require('@daytona/sdk/v0')

// The v0 client is built only through the root. CommonJS: calling or
// constructing that export throws an error naming the root form; it is not
// enumerable.
assert.throws(() => new v0.Daytona({ apiKey: 'k' }), /not exported from '@daytona\/sdk\/v0'.*new Daytona\(\)\.v0.*stay on SDK 0\.x/)
assert.ok(!Object.keys(v0).includes('Daytona'), 'Daytona is an enumerable v0 export')
assert.equal(v0.DaytonaConfig, undefined, 'DaytonaConfig is a v0 runtime export')
// ES modules: no `Daytona` named export, so `import { Daytona } from
// '@daytona/sdk/v0'` fails at link time.
{
  const run = spawnSync(process.execPath, ['--input-type=module', '-e', "import { Daytona } from '@daytona/sdk/v0'"], { encoding: 'utf8', cwd: process.cwd() })
  assert.notEqual(run.status, 0, 'ESM import { Daytona } from @daytona/sdk/v0 linked')
  assert.match(run.stderr, /Named export 'Daytona' not found|does not provide an export named 'Daytona'/)
}
// Nor is the v2 client built directly: CommonJS throws naming the root
// form for `Daytona` and `DaytonaV2`; ES modules have no such export.
{
  const v2 = require('@daytona/sdk/v2')
  for (const name of ['Daytona', 'DaytonaV2']) {
    assert.throws(() => new v2[name]({ apiKey: 'k' }), /not exported from '@daytona\/sdk\/v2'.*new Daytona\(\)\.v2/, `CJS v2.${name}`)
    assert.ok(!Object.keys(v2).includes(name), `${name} is an enumerable v2 export`)
  }
  for (const name of ['Daytona', 'DaytonaV2']) {
    const run = spawnSync(process.execPath, ['--input-type=module', '-e', `import { ${name} } from '@daytona/sdk/v2'`], { encoding: 'utf8', cwd: process.cwd() })
    assert.notEqual(run.status, 0, `ESM import { ${name} } from @daytona/sdk/v2 linked`)
    assert.match(run.stderr, new RegExp(`does not provide an export named '${name}'|Named export '${name}' not found`))
  }
}

/** The root's v0 client (the only way to build one) against `apiUrl`, through
 * the non-production DAYTONA_V0_API_URL; restores the variable afterwards. */
function rootV0At(apiUrl) {
  const saved = process.env.DAYTONA_V0_API_URL
  process.env.DAYTONA_V0_API_URL = apiUrl
  try {
    return new root.Daytona({ apiKey: 'smoke' }).v0
  } finally {
    if (saved === undefined) delete process.env.DAYTONA_V0_API_URL
    else process.env.DAYTONA_V0_API_URL = saved
  }
}

async function withRecordingApi(run) {
  const seen = []
  const server = http.createServer((req, res) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      seen.push({ method: req.method, path: req.url, headers: req.headers, body: raw ? JSON.parse(raw) : {} })
      if (req.method === 'GET' && req.url.startsWith('/sandbox')) {
        // An empty list page: lets v0's list() iterator run to completion.
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ items: [], nextCursor: null }))
        return
      }
      res.writeHead(400, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ statusCode: 400, message: 'recorded', error: 'Bad Request' }))
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  try {
    await run(`http://127.0.0.1:${server.address().port}`, seen)
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
}

async function main() {
  // FIRST, before any d.v0 access: an error class imported from
  // '@daytona/sdk/v0' is caught by the root classes (the v0 entry registers
  // itself), from CJS and ESM.
  assert.ok(new v0.DaytonaNotFoundError('direct') instanceof root.DaytonaNotFoundError, 'CJS direct v0 import: DaytonaNotFoundError')
  assert.ok(new v0.DaytonaNotFoundError('direct') instanceof root.DaytonaError, 'CJS direct v0 import: DaytonaError')
  const esmRoot = await import('@daytona/sdk')
  const esmV0 = await import('@daytona/sdk/v0')
  const esmV0Error = new (esmV0.DaytonaNotFoundError ?? esmV0.default.DaytonaNotFoundError)('direct')
  assert.ok(esmV0Error instanceof esmRoot.DaytonaNotFoundError, 'ESM direct v0 import: DaytonaNotFoundError')
  assert.ok(esmV0Error instanceof esmRoot.DaytonaError, 'ESM direct v0 import: DaytonaError')
  assert.ok(!(esmV0Error instanceof esmRoot.DaytonaConflictError), 'ESM direct v0 import: not DaytonaConflictError')
  // The v2 classes across their ESM and CJS copies (shared registry keyed by
  // class, see src/v2/errors/DaytonaError.ts), in both directions.
  const cjsV2 = require('@daytona/sdk/v2')
  const esmV2 = await import('@daytona/sdk/v2')
  assert.notEqual(cjsV2.NotFoundError, esmV2.NotFoundError, 'expected two copies of @daytona/sdk/v2')
  assert.ok(new cjsV2.NotFoundError('cjs') instanceof esmV2.NotFoundError, 'CJS v2 error instanceof ESM class')
  assert.ok(new esmV2.NotFoundError('esm') instanceof cjsV2.NotFoundError, 'ESM v2 error instanceof CJS class')
  assert.ok(new cjsV2.ProcessNotFoundError('cjs') instanceof esmV2.NotFoundError, 'CJS v2 subclass instanceof ESM base')
  assert.ok(new cjsV2.NotFoundError('cjs') instanceof esmV2.DaytonaError, 'CJS v2 error instanceof ESM DaytonaError')
  assert.ok(!(new cjsV2.NotFoundError('cjs') instanceof esmV2.ConflictError), 'no cross-category match')

  // v0's own license terms ship next to both v0 builds.
  for (const dir of ['cjs', 'esm']) {
    for (const name of ['LICENSE', 'NOTICE']) {
      const file = path.join(path.dirname(require.resolve('@daytona/sdk/v0')), '..', '..', dir, 'v0', name)
      assert.ok(require('node:fs').existsSync(file), `missing ${file}`)
    }
  }

  const pinned = process.argv[2]
  // _bundle is provenance, not an export: load it beside the v0 entry point.
  const bundle = require(path.join(path.dirname(require.resolve('@daytona/sdk/v0')), '_bundle.js'))
  if (pinned) assert.equal(bundle.BUNDLED_FROM, `daytona/clients@${pinned}`)

  const d = new root.Daytona({ apiKey: 'smoke' })
  assert.ok(d.v0 instanceof v0.RootOnlyClientV0, 'd.v0 is the bundled v0 Daytona')
  assert.equal(d.v0, d.v0, 'd.v0 is cached')

  await withRecordingApi(async (apiUrl, seen) => {
    const client = rootV0At(apiUrl)
    await assert.rejects(client.create({ target: 'eu' }, { timeout: 5 }))
    const create = seen.filter((r) => r.method === 'POST' && r.path.startsWith('/sandbox')).pop()
    assert.ok(create, `no sandbox create recorded: ${JSON.stringify(seen)}`)
    assert.equal(create.body.target, 'eu')

    await assert.rejects(client.snapshot.create({ name: 'snap', image: 'python:3.12', regionId: 'region-a' }))
    const snapshot = seen.filter((r) => r.method === 'POST' && r.path.startsWith('/snapshots')).pop()
    assert.ok(snapshot, `no snapshot create recorded: ${JSON.stringify(seen)}`)
    assert.equal(snapshot.body.regionId, 'region-a')
  })

  // v0's list() is a SYNC method returning an async iterator: through d.v0 it
  // is v0's own iterator, unchanged. (d.v0 targets the production v0 URL,
  // so nothing here iterates it: creating the generator makes no request.)
  const listed = d.v0.list()
  assert.equal(typeof listed.then, 'undefined')
  assert.equal(typeof listed[Symbol.asyncIterator], 'function')
  assert.equal(typeof d.v0.snapshot.list, 'function')
  // And the v0 iterator itself runs to completion against the mock.
  await withRecordingApi(async (apiUrl, seen) => {
    const listed = []
    for await (const sandbox of rootV0At(apiUrl).list()) listed.push(sandbox)
    assert.deepEqual(listed, [])
    assert.ok(seen.some((r) => r.method === 'GET' && r.path.startsWith('/sandbox')), JSON.stringify(seen))
    // v0 reports its own release, not undefined (the bundle rewrites its
    // package.json lookup): X-Daytona-SDK-Version and User-Agent.
    const listCall = seen.find((r) => r.method === 'GET' && r.path.startsWith('/sandbox'))
    const bundledVersion = bundle.BUNDLED_VERSION
    assert.ok(bundledVersion, '_bundle.BUNDLED_VERSION is set')
    if (pinned) assert.equal(bundledVersion, pinned.replace(/^v/, ''))
    assert.equal(listCall.headers['x-daytona-sdk-version'], bundledVersion)
    assert.ok(!String(listCall.headers['user-agent']).includes('undefined'), `user-agent: ${listCall.headers['user-agent']}`)
    assert.ok(String(listCall.headers['user-agent']).endsWith(`/${bundledVersion}`), `user-agent: ${listCall.headers['user-agent']}`)
  })

  // Daytona().v0 builds the real bundled client against DAYTONA_V0_API_URL,
  // never the 0.x SDK's DAYTONA_API_URL.
  await withRecordingApi(async (apiUrl, seen) => {
    const saved = { v0: process.env.DAYTONA_V0_API_URL, v2: process.env.DAYTONA_API_URL }
    process.env.DAYTONA_V0_API_URL = apiUrl
    process.env.DAYTONA_API_URL = 'http://v2-only.invalid'
    try {
      const viaEnv = new root.Daytona({ apiKey: 'smoke' })
      await assert.rejects(viaEnv.v0.create({}, { timeout: 5 }))
      assert.ok(seen.some((r) => r.method === 'POST' && r.path.startsWith('/sandbox')), JSON.stringify(seen))
    } finally {
      if (saved.v0 === undefined) delete process.env.DAYTONA_V0_API_URL
      else process.env.DAYTONA_V0_API_URL = saved.v0
      if (saved.v2 === undefined) delete process.env.DAYTONA_API_URL
      else process.env.DAYTONA_API_URL = saved.v2
    }
  })
  const error = new v0.DaytonaNotFoundError('x')
  assert.ok(error instanceof root.DaytonaError)
  assert.ok(error instanceof root.DaytonaNotFoundError)

  // v0's dotenv warning names remedies that work under the root: a .env asking
  // for another endpoint must not be answered with "pass apiUrl / set
  // DAYTONA_API_URL" (the root rejects apiUrl; daytona.v0 ignores the variable).
  {
    const fs = require('node:fs')
    const os = require('node:os')
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'daytona-dotenv-'))
    fs.writeFileSync(path.join(dir, '.env'), 'DAYTONA_API_URL=http://dotenv.invalid\n')
    const entry = require.resolve('@daytona/sdk')
    const env = { ...process.env }
    delete env.DAYTONA_API_URL
    delete env.DAYTONA_V0_API_URL
    const run = spawnSync(
      process.execPath,
      ['-e', `const { Daytona } = require(${JSON.stringify(entry)}); void new Daytona({ apiKey: 'k' }).v0`],
      { encoding: 'utf8', cwd: dir, env },
    )
    assert.equal(run.status, 0, `dotenv child failed: ${run.stderr}`)
    const warning = `${run.stdout}\n${run.stderr}`
    if (warning.includes('was ignored')) {
      assert.ok(warning.includes('For a custom API URL, stay on SDK 0.x.'), `dotenv warning: ${warning}`)
      assert.ok(!warning.includes("from '@daytona/sdk/v0'"), `dotenv warning: ${warning}`)
      assert.ok(warning.includes('DAYTONA_V0_API_URL'), `dotenv warning: ${warning}`)
      assert.ok(!warning.includes('pass `apiUrl` to the Daytona constructor'), `dotenv warning: ${warning}`)
    } else {
      // dotenv is optional in v0 (loaded only when installed). sdk-ci installs
      // it and sets DAYTONA_SMOKE_REQUIRE_DOTENV, so there the check cannot skip.
      assert.ok(!process.env.DAYTONA_SMOKE_REQUIRE_DOTENV, `dotenv warning expected but not printed: ${warning}`)
      console.log('dotenv not installed: dotenv warning not exercised')
    }
    fs.rmSync(dir, { recursive: true, force: true })
  }

  console.log(`v0 namespace ok: ${bundle.BUNDLED_FROM}`)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
