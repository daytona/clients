/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Serves the vite build on a free port and loads it in headless Chromium.
// A page has no environment, so the configuration is injected ahead of the
// bundle: the API key as a page global, and DAYTONA_V2_API_URL through a
// `process.env` shim, which is where the SDK reads it (as it would from any
// process polyfill an application installs).
import { readFileSync, existsSync } from 'node:fs'
import { createServer } from 'node:http'
import { extname, join, resolve, sep } from 'node:path'
import { chromium } from 'playwright'

const DIST = resolve('dist')
const MIME = { '.js': 'text/javascript', '.html': 'text/html', '.css': 'text/css' }
const { DAYTONA_V2_API_URL, DAYTONA_API_KEY } = process.env
if (!DAYTONA_V2_API_URL || !DAYTONA_API_KEY) {
  console.error('DAYTONA_V2_API_URL and DAYTONA_API_KEY must be set (run via run-all.sh)')
  process.exit(1)
}
const inject = `<script>
  window.process = { env: { DAYTONA_V2_API_URL: ${JSON.stringify(DAYTONA_V2_API_URL)} } };
  window.__DAYTONA_API_KEY__ = ${JSON.stringify(DAYTONA_API_KEY)};
</script>`

const server = createServer((req, res) => {
  let path = new URL(req.url || '/', 'http://localhost').pathname
  if (path === '/favicon.ico') {
    res.writeHead(204)
    res.end()
    return
  }
  if (path === '/') path = '/index.html'
  const file = resolve(join(DIST, path))
  if (!file.startsWith(DIST + sep) || !existsSync(file)) {
    res.writeHead(404)
    res.end()
    return
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' })
  const body = readFileSync(file)
  res.end(extname(file) === '.html' ? body.toString('utf8').replace('<head>', `<head>${inject}`) : body)
})
await new Promise((ready) => server.listen(0, '127.0.0.1', ready))

const browser = await chromium.launch({ headless: true })
let output = ''
try {
  const page = await browser.newPage()
  const logs = []
  page.on('console', (message) => logs.push(`[${message.type()}] ${message.text()}`))
  page.on('pageerror', (error) => logs.push(`[pageerror] ${error.message}`))
  await page.goto(`http://127.0.0.1:${server.address().port}/`)
  await page.waitForFunction(() => document.body.dataset.result !== undefined, null, { timeout: 30000 }).catch(() => undefined)
  output = (await page.evaluate(() => document.body.dataset.result)) ?? ''
  if (logs.length > 0) console.error(logs.join('\n'))
} finally {
  await browser.close()
  server.close()
}
console.log(output)
