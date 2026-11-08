#!/usr/bin/env -S npx tsx
/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { randomUUID } from 'node:crypto'
import { Daytona } from '../sdk-typescript/src/index'
import { DaytonaError, ImageBuilder, RequestTimeoutError, type Image, type Sandbox } from '../sdk-typescript/src/v2/index'

const run = randomUUID().replace(/-/g, '').slice(0, 8)
const image = process.env.SMOKE_IMAGE ?? 'python:3.12-slim'
const name = `daytona-ts-${run}`
const dockerName = `daytona-ts-df-${run}`
const dockerfile = `FROM ${image}\nRUN echo daytona-built-${run} > /etc/daytona-marker\n`
const keep = !!process.env.DAYTONA_KEEP
const skipToolbox = !!process.env.DAYTONA_SKIP_TOOLBOX
// Skips the Dockerfile build + boot (reported as SKIP, never PASS) on
// deployments without image builds.
const skipDockerfile = !!process.env.DAYTONA_SKIP_DOCKERFILE
const boxes: Sandbox[] = []
const images: Image[] = []
let pass = 0
let fail = 0
let skipped = 0
const apiKey = process.env.DAYTONA_API_KEY

if (!apiKey) {
  console.error('DAYTONA_API_KEY is required (create a project API key in the Daytona dashboard)')
  process.exit(2)
}

// The v2 client reads DAYTONA_V2_API_URL itself (default: https://api.daytona.com).

const daytona = new Daytona({
  apiKey,
}).v2

function check(label: string, condition: boolean, detail = ''): void {
  if (condition) {
    pass++
    console.log(`PASS ${label}${detail ? ` (${detail})` : ''}`)
  } else {
    fail++
    console.log(`FAIL ${label}${detail ? ` (${detail})` : ''}`)
  }
}

// One `images.get({ wait: 'ready' })` with the whole budget: it repeats the
// server's long poll and returns the image once it settles (`ready`, `failed`
// or `error`), or throws `RequestTimeoutError` when the budget runs out,
// reported here as a failed check.
async function waitReady(imageId: string, timeoutSeconds = 900): Promise<string> {
  try {
    return (await daytona.images.get(imageId, { wait: 'ready', timeoutSeconds })).status
  } catch (error) {
    if (error instanceof RequestTimeoutError) return `not settled within ${timeoutSeconds} s`
    throw error
  }
}

async function expectError(label: string, fn: () => Promise<unknown>, status?: number): Promise<void> {
  try {
    await fn()
    check(label, false, 'expected error, got success')
  } catch (error) {
    const got = error instanceof DaytonaError ? error.statusCode : undefined
    check(label, status === undefined || got === status, `status=${got}`)
  }
}

async function main(): Promise<number> {
  try {
    const pulled = await daytona.images.pull(image, { name })
    images.push(pulled)
    check('image pull accepted', !!pulled.id && pulled.name === name)
    let built: Image | undefined
    if (skipDockerfile) {
      skipped++
      console.log('SKIP dockerfile image build + boot (DAYTONA_SKIP_DOCKERFILE)')
    } else {
      built = await daytona.images.build(ImageBuilder.fromDockerfile(dockerfile), { name: dockerName })
      images.push(built)
      check('dockerfile image accepted', !!built.id && built.name === dockerName)
    }
    const listed = await daytona.images.list()
    check('images list returns array', Array.isArray(listed.items))
    for (const entry of built ? [pulled, built] : [pulled]) {
      const status = await waitReady(entry.id)
      check(`image ${entry.name} ready`, status === 'ready', status)
    }

    const box = await daytona.create(name)
    boxes.push(box)
    check('sandbox started', box.state === 'started', box.id)
    if (built) {
      const dockerBox = await daytona.create(dockerName)
      boxes.push(dockerBox)
      check('dockerfile sandbox started', dockerBox.state === 'started', dockerBox.id)
      if (!skipToolbox && dockerBox.state === 'started') {
        const marker = await dockerBox.process.run('cat /etc/daytona-marker')
        check('dockerfile image marker', marker.exitCode === 0 && marker.output.includes(`daytona-built-${run}`), marker.output.trim())
      }
    }
    check('get(id)', (await daytona.get(box.id)).id === box.id)
    check('list includes sandbox', (await daytona.list()).items.some((item) => item.id === box.id))

    if (!skipToolbox && box.state === 'started') {
      const result = await box.process.run('echo daytona-$((6*7))')
      check('process.run', result.exitCode === 0 && result.output.includes('daytona-42'), result.output.trim())
    }

    await expectError('create(unknown image) 404', () => daytona.create(`no-such-${run}`), 404)
  } finally {
    if (!keep) {
      for (const box of boxes) {
        try {
          await daytona.delete(box)
        } catch (error) {
          console.error(`box delete failed: ${String(error instanceof Error ? error.message : error).slice(0, 80)}`)
          fail++
        }
      }
      // Image references can still be releasing after sandbox deletion has
      // completed; retry conflicts for a bounded time.
      for (const entry of images) {
        for (let attempt = 0; attempt < 60; attempt++) {
          try {
            await daytona.images.delete(entry.id)
            break
          } catch (error) {
            const status = error instanceof DaytonaError ? error.statusCode : undefined
            if (status !== 409 || attempt === 59) {
              console.error(`image delete failed: ${String(error instanceof Error ? error.message : error).slice(0, 80)}`)
              fail++
              break
            }
            await new Promise((resolve) => setTimeout(resolve, 2000))
          }
        }
      }
    }
  }
  console.log(`${pass} passed, ${fail} failed, ${skipped} skipped`)
  return fail === 0 ? 0 : 1
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error(error)
  process.exit(1)
})
