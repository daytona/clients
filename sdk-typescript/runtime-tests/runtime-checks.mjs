/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The checks every runtime runs, against mock-api.mjs. run-all.sh copies this
// file (and its .d.mts) into each runtime's working copy; the runtime imports
// it and passes in the `@daytona/sdk` module it loaded the way that runtime
// loads packages, so the SDK build under test is the runtime's own choice
// (CJS, ESM, browser, worker, ...), never this file's.
//
// Both SDK transports are exercised: the API calls and `fs.list` go through
// axios (Node's http adapter where the runtime has one, the SDK's fetch
// adapter elsewhere); `fs.watchDir`
// goes through the native fetch transport (ReadableStream reader and
// AbortController, the APIs that differ most across runtimes).

const SANDBOX_ID = '7c0d5f8e-2b1a-4c3d-9e8f-0a1b2c3d4e5f'

function describe(error) {
  if (error instanceof Error) return `${error.name}: ${error.message}`
  return String(error)
}

async function checkV0(daytona) {
  // The root carries the v0 namespace in published (bundled) builds; source
  // builds carry a stub whose access throws NOT_IMPLEMENTED (501).
  try {
    return typeof daytona.v0.list === 'function' ? 'bundled' : 'v0 client has no list()'
  } catch (error) {
    if (error && error.code === 'NOT_IMPLEMENTED' && error.statusCode === 501) return 'unbundled'
    return `v0 failed: ${describe(error)}`
  }
}

function isTooLarge(error) {
  return error instanceof Error && error.statusCode === 413 && error.code === 'PAYLOAD_TOO_LARGE' && error.source === 'SDK'
}

/**
 * `promise`, or a rejection after `ms`. The calls it wraps carry no signal of
 * their own (the SDK's list() and readBytes() take none), so the client they
 * run on bounds them itself (`requestTimeoutSeconds` below, shorter than `ms`):
 * a runtime that buffers the body first still ends the request, and nothing
 * keeps the runtime process alive.
 */
function within(promise, ms, what) {
  let timer
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error(`${what} did not settle within ${ms} ms`)), ms) }),
  ])
}

export async function runChecks(sdk, options) {
  const result = { listOk: false, notFoundOk: false, toolboxListOk: false, toolboxWatchOk: false, capOk: false, v0: 'unchecked' }
  const daytona = new sdk.Daytona({ apiKey: options.apiKey })
  try {
    const client = daytona.v2
    let box
    try {
      const { items } = await client.list()
      box = items[0]
      result.listOk = items.length === 1 && box.id === SANDBOX_ID && box.state === 'started'
      if (!result.listOk) result.listError = JSON.stringify(items.map((item) => [item.id, item.state]))
    } catch (error) {
      result.listError = describe(error)
    }

    try {
      await client.get('does-not-exist')
      result.notFoundError = 'get() of an unknown id resolved'
    } catch (error) {
      result.notFoundOk = error instanceof sdk.DaytonaNotFoundError && error.statusCode === 404 && error.code === 'NOT_FOUND'
      if (!result.notFoundOk) result.notFoundError = describe(error)
    }

    if (box) {
      try {
        const files = await box.fs.list('/')
        result.toolboxListOk = files.items.length === 1 && files.items[0].name === 'hello.txt'
        if (!result.toolboxListOk) result.toolboxListError = JSON.stringify(files)
      } catch (error) {
        result.toolboxListError = describe(error)
      }

      try {
        const seen = []
        const watch = await box.fs.watchDir('/', (event) => {
          seen.push(event.name)
        })
        for (let i = 0; i < 100 && seen.length === 0; i++) await new Promise((resolve) => setTimeout(resolve, 50))
        await watch.stop()
        const exitError = await watch.waitForExit()
        result.toolboxWatchOk = seen.length === 1 && seen[0] === 'hello.txt' && exitError === undefined && watch.exitError === undefined
        if (!result.toolboxWatchOk) result.toolboxWatchError = JSON.stringify({ seen, exitError: exitError && describe(exitError) })
      } catch (error) {
        result.toolboxWatchError = describe(error)
      }
    }

    // The buffered-body cap, in this runtime's transport: an API JSON body
    // over a small client cap, and a toolbox download that declares more
    // than the per-call cap, refused before its body is read.
    // requestTimeoutSeconds bounds both calls: list() by its total deadline,
    // readBytes() (a transfer) by its idle bound, since the mock stops sending.
    const capped = new sdk.Daytona({ apiKey: options.apiKey, maxResponseBytes: 64, requestTimeoutSeconds: 3 })
    try {
      const listError = await within(capped.v2.list(), 5_000, 'list()').then(() => undefined, (error) => error)
      const downloadError = box
        ? await within(box.fs.readBytes('/big.bin', { maxResponseBytes: 1024 }), 5_000, 'readBytes()').then(() => undefined, (error) => error)
        : new Error('no sandbox to read from')
      result.capOk = isTooLarge(listError) && isTooLarge(downloadError)
      if (!result.capOk) result.capError = JSON.stringify({ list: listError && describe(listError), download: downloadError && describe(downloadError) })
    } finally {
      await capped.close()
    }

    result.v0 = await checkV0(daytona)
  } finally {
    await daytona.close()
  }
  return result
}
