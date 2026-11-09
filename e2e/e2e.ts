#!/usr/bin/env -S npx tsx
/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { randomUUID } from 'node:crypto'
import { Daytona } from '../sdk-typescript/src/index'
import { DaytonaError, ImageBuilder, UnsupportedOperationError, type Image, type ProcessInfo, type Sandbox } from '../sdk-typescript/src/v2/index'

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

// `images.get({ wait: 'ready' })` long-polls at most 60 s per call: repeat it
// until the image settles or the overall deadline passes.
async function waitReady(imageId: string, deadlineMs = 900_000): Promise<string> {
  const deadline = Date.now() + deadlineMs
  for (;;) {
    const { status } = await daytona.images.get(imageId, { wait: 'ready', timeout: 60 })
    if (status === 'ready' || status === 'failed' || Date.now() >= deadline) return status
    await new Promise((resolve) => setTimeout(resolve, 1000))
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
      if (!skipToolbox) {
        const marker = await dockerBox.process.run('cat /etc/daytona-marker')
        check('dockerfile image marker', marker.exitCode === 0 && marker.output.includes(`daytona-built-${run}`), marker.output.trim())
      }
    }
    check('get(id)', (await daytona.get(box.id)).id === box.id)
    check('list includes sandbox', (await daytona.list()).items.some((item) => item.id === box.id))

    if (!skipToolbox) {
      const result = await box.process.run('echo daytona-$((6*7))')
      check('process.run', result.exitCode === 0 && result.output.includes('daytona-42'), result.output.trim())

      // Given two blocked roots sharing a tag and one independent root.
      const tag = `sdk-ts-${run}`
      const otherTag = `sdk-ts-other-${run}`
      const first = await box.process.start('read line', { stdin: 'open', tag })
      const second = await box.process.start('read line', { stdin: 'open', tag })
      const unrelated = await box.process.start('read line', { stdin: 'open', tag: otherTag })
      const tagged = await box.process.list({ tag })
      const taggedIds = new Set(tagged.items.map((entry: ProcessInfo): string => entry.id))
      check('process.list filters shared tag', taggedIds.size === 2 && taggedIds.has(first.id) && taggedIds.has(second.id)
        && tagged.items.every((entry: ProcessInfo): boolean => entry.tag === tag))
      const other = await box.process.list({ tag: otherTag })
      check('process.list filters unrelated tag', other.items.length === 1 && other.items[0]?.id === unrelated.id)

      // A tagged command that leaves a detached child behind and exits.
      await box.process.run(`setsid sh -c 'echo $$ > /tmp/dtn-tag-${run}; exec sleep 300' >/dev/null 2>&1 & sleep 0.5`, { tag })
      const detached = (await box.process.run(`cat /tmp/dtn-tag-${run}`)).output.trim()
      check('detached child started', /^\d+$/.test(detached), JSON.stringify(detached))

      // When the shared tag is killed, both roots exit but the other can finish.
      // A sandbox without process containment refuses the tag-wide kill.
      let killed: Set<string> | undefined
      try {
        killed = new Set(await box.process.killAll({ tag, timeoutSeconds: 10 }))
      } catch (error) {
        if (!(error instanceof UnsupportedOperationError)) throw error
        skipped++
        console.log('SKIP process.killAll tag-kill checks (no process containment on this sandbox)')
        for (const root of [first, second, unrelated]) await root.kill()
        // Without containment kill() does not reach the detached child either.
        if (detached) await box.process.run(`kill -9 ${detached} 2>/dev/null; true`)
      }
      if (killed) {
        check('process.killAll returns root ids', killed.has(first.id) && killed.has(second.id))
        const alive = await box.process.run(`kill -0 ${detached} 2>/dev/null && echo alive || echo gone`)
        check('detached descendant killed', alive.output.trim() === 'gone', detached)
        for (const root of [first, second]) {
          const stopped = await root.wait({ timeoutSeconds: 5 })
          check('tagged root killed', stopped.reason === 'signaled' && stopped.signal === 'KILL', root.id)
        }
        await unrelated.writeStdin('finish\n') // read returns; the root exits 0
        const survivor = await unrelated.wait({ timeoutSeconds: 5 })
        check('unrelated root completes normally', survivor.reason === 'exited' && survivor.exitCode === 0)

        // A fresh command with the killed tag is new work, not fenced out.
        const reused = await box.process.run('echo tag-reused', { tag, timeoutSeconds: 5 })
        check('process tag reusable', reused.exitCode === 0 && reused.output.includes('tag-reused'))
      }
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
      // Sandbox delete is asynchronous: the image stays referenced (409) until
      // the sandboxes are gone, so retry for a bounded time.
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
