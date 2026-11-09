/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Local stand-in for the Daytona API and one sandbox's toolbox, shaped after
// contract/api-openapi.json and contract/toolbox-openapi.json. The runtime
// compatibility tests point every runtime at it, so they need no network
// beyond the package registry and no credentials.
//
// API (axios transport)
//   GET  /sandboxes                         -> SandboxPage with one `running` sandbox
//   GET  /sandboxes/{id}                    -> that Sandbox, or a 404 ErrorBody
//   POST /sandboxes/{id}/access-token       -> 201 SandboxAccessToken
// Toolbox, under the sandbox's `toolbox_proxy_url` (`/toolbox/{id}`)
//   GET  /files?path=/                      -> [FileInfo] (axios transport)
//   GET  /files/watch?path=/                -> SSE `start`, one `fs` event, then
//                                              held open until the client aborts
//                                              (fetch transport + ReadableStream
//                                              + AbortController)
//
// Every API request must carry `Authorization: Bearer <MOCK_API_KEY>`; every
// toolbox request the sandbox access token minted above. Every request must
// also carry `X-Daytona-SDK-Version: <MOCK_SDK_VERSION>`: run-all.sh stamps a
// version unique to the run into the packed tarball, so a runtime that loaded
// any other build of @daytona/sdk fails every call instead of passing on the
// wrong code. CORS is open for the browser runtime.
//
// Prints `LISTENING <port>` on stdout once bound (MOCK_API_PORT, default 0 =
// a free port).

import { randomBytes } from 'node:crypto'
import { createServer } from 'node:http'

const API_KEY = process.env.MOCK_API_KEY
const SDK_VERSION = process.env.MOCK_SDK_VERSION
if (!API_KEY || !SDK_VERSION) {
  console.error('MOCK_API_KEY and MOCK_SDK_VERSION are required (run via run-all.sh)')
  process.exit(2)
}
const PORT = Number(process.env.MOCK_API_PORT || 0)

// Mirrored by runtime-checks.mjs.
const SANDBOX_ID = '7c0d5f8e-2b1a-4c3d-9e8f-0a1b2c3d4e5f'
const SANDBOX_TOKEN = `sbx_${randomBytes(16).toString('hex')}`

function sandbox(origin) {
  return {
    id: SANDBOX_ID,
    name: 'runtime-test',
    status: 'running',
    class: 'linux',
    cpu: 1,
    cores_max: 1,
    memory_mib: 1024,
    disk_mib: 2048,
    port_map: [],
    region_id: 'local',
    labels: { purpose: 'runtime-test' },
    image_ref: 'alpine:3.20',
    created_at: '2026-01-01T00:00:00Z',
    toolbox_proxy_url: `${origin}/toolbox`,
  }
}

const FILE_INFO = {
  name: 'hello.txt',
  path: '/hello.txt',
  size: 12,
  mode: '-rw-r--r--',
  modTime: 'Thu Jan  1 00:00:00 UTC 2026',
  modifiedAt: '2026-01-01T00:00:00Z',
  isDir: false,
  owner: '1000',
  group: '1000',
  permissions: '0644',
}

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, POST, PATCH, DELETE, OPTIONS',
  'access-control-max-age': '600',
}

function send(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json', ...CORS })
  res.end(body === undefined ? '' : JSON.stringify(body))
}

function sendError(res, status, code, message, source = 'DAYTONA_API') {
  send(res, status, { code, message, source })
}

function sendWatchStream(req, res) {
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', ...CORS })
  res.write('event: start\ndata: {}\n\n')
  const event = { type: 'create', name: 'hello.txt', isDir: false, timestamp: Date.now() }
  res.write(`event: fs\ndata: ${JSON.stringify(event)}\n\n`)
  // Held open: the client has to abort it. A runtime whose AbortController
  // does not reach the connection hangs here instead of passing.
  const keepalive = setInterval(() => res.write(': keepalive\n\n'), 1000)
  req.on('close', () => clearInterval(keepalive))
}

// Declares 1 MiB and sends 16 bytes, then nothing: a client that checks the
// declared length refuses it at once, one that buffers first hangs here
// (the runtime check has its own deadline).
function sendOversizedDownload(req, res) {
  res.writeHead(200, { ...CORS, 'content-type': 'application/octet-stream', 'content-length': String(1 << 20) })
  res.write('x'.repeat(16))
  const hangUp = setTimeout(() => res.destroy(), 15_000)
  req.on('close', () => clearTimeout(hangUp))
}

const server = createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://mock.invalid')
  const path = url.pathname
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { ...CORS, 'access-control-allow-headers': req.headers['access-control-request-headers'] || '' })
    res.end()
    return
  }
  const sdkVersion = req.headers['x-daytona-sdk-version']
  if (sdkVersion !== SDK_VERSION) {
    sendError(res, 400, 'BAD_REQUEST', `request came from @daytona/sdk ${sdkVersion ?? '<no X-Daytona-SDK-Version>'}, expected the build under test ${SDK_VERSION}`)
    return
  }

  const toolbox = path.match(/^\/toolbox\/([^/]+)(\/.*)?$/)
  if (toolbox) {
    if (decodeURIComponent(toolbox[1]) !== SANDBOX_ID) {
      sendError(res, 404, 'NOT_FOUND', `no sandbox ${toolbox[1]}`, 'DAYTONA_PROXY')
      return
    }
    if (req.headers.authorization !== `Bearer ${SANDBOX_TOKEN}`) {
      sendError(res, 401, 'UNAUTHORIZED', 'missing or invalid sandbox access token', 'DAYTONA_PROXY')
      return
    }
    const route = toolbox[2] || '/'
    if (req.method === 'GET' && route === '/files' && url.searchParams.get('path') === '/') return send(res, 200, [FILE_INFO])
    if (req.method === 'GET' && route === '/files/watch' && url.searchParams.get('path') === '/') return sendWatchStream(req, res)
    if (req.method === 'GET' && route === '/files/download' && url.searchParams.get('path') === '/big.bin') return sendOversizedDownload(req, res)
    sendError(res, 404, 'NOT_FOUND', `the mock does not serve ${req.method} ${route}${url.search}`, 'DAYTONA_DAEMON')
    return
  }

  if (req.headers.authorization !== `Bearer ${API_KEY}`) {
    sendError(res, 401, 'UNAUTHORIZED', 'missing or invalid API key')
    return
  }
  const origin = `http://${req.headers.host}`
  if (req.method === 'GET' && path === '/sandboxes') {
    return send(res, 200, { sandboxes: [{ ...sandbox(origin), cursor: 'c1' }], next_cursor: null })
  }
  const one = path.match(/^\/sandboxes\/([^/]+)(\/access-token)?$/)
  if (one) {
    const id = decodeURIComponent(one[1])
    if (id !== SANDBOX_ID) return sendError(res, 404, 'NOT_FOUND', `sandbox ${id} not found`)
    if (req.method === 'GET' && !one[2]) return send(res, 200, sandbox(origin))
    if (req.method === 'POST' && one[2]) {
      req.resume()
      return send(res, 201, {
        access_token: SANDBOX_TOKEN,
        token_type: 'Bearer',
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        operations: ['toolbox'],
        ports: null,
      })
    }
  }
  sendError(res, 404, 'NOT_FOUND', `the mock does not serve ${req.method} ${path}`)
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`LISTENING ${server.address().port}`)
})
