/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Share a volume across sandboxes and verify data survives sandbox deletion.
 * Sandbox A writes, B reads and appends, then C reads both writers' data.
 * Writes synchronize asynchronously; EXAMPLE_SYNC_WAIT_SECONDS sets the wait in
 * seconds before deleting a writer (default 20). Run from examples:
 *
 *   DAYTONA_API_KEY=dtn_... npx tsx volumes_share.ts
 *
 * DAYTONA_V2_API_URL (a regional endpoint or test environment) defaults to https://api.daytona.com. DAYTONA_IMAGE defaults
 * to python:3.12-slim. Set DAYTONA_KEEP=1 to retain resources for inspection.
 */

import { randomUUID } from 'node:crypto'
import { Daytona, DaytonaError } from '@daytona/sdk'
import { ImageBuilder } from '@daytona/sdk/v2'
import type { Sandbox } from '@daytona/sdk/v2'

const IMAGE = process.env.DAYTONA_IMAGE ?? 'python:3.12-slim'
const SYNC_WAIT_SECONDS = Number(process.env.EXAMPLE_SYNC_WAIT_SECONDS ?? '20')
const KEEP = Boolean(process.env.DAYTONA_KEEP)
const RUN = randomUUID().replace(/-/g, '').slice(0, 8)
const sleep = (seconds: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, seconds * 1000))

async function main(): Promise<number> {
  const daytona = new Daytona({ apiKey: process.env.DAYTONA_API_KEY }).v2
  const volume = await daytona.volume.create(`shared-vol-${RUN}`)
  console.log(`volume id=${volume.id} name=${volume.name} status=${volume.status}`)
  const boxes: Sandbox[] = []
  let ok = true

  // Run a shell command and return trimmed stdout; throw if it exits non-zero.
  const sh = async (box: Sandbox, command: string): Promise<string> => {
    const result = await box.process.run(command)
    const output = result.output.trim()
    if (result.exitCode !== 0) throw new Error(`${JSON.stringify(command)} failed (exit=${result.exitCode}): ${output}`)
    return output
  }
  const newBox = async (name: string): Promise<Sandbox> => {
    const box = await daytona.createFromBuild(ImageBuilder.fromRegistry(IMAGE), {
      name: `${name}-${RUN}`, volumes: [{ volumeId: volume.id, mountPath: '/data' }], waitSeconds: 240,
    })
    boxes.push(box)
    return box
  }

  try {
    console.log('1. sandbox A writes /data/message.txt')
    const a = await newBox('vol-a')
    const marker = `hello-from-A-${RUN}`
    await sh(a, `echo ${marker} > /data/message.txt && sync`)
    console.log(`waiting ${SYNC_WAIT_SECONDS}s for A's changes to synchronize`)
    await sleep(SYNC_WAIT_SECONDS)
    await a.delete()
    boxes.splice(boxes.indexOf(a), 1)
    await sleep(3)

    console.log("2. sandbox B reads A's file and appends a line")
    const b = await newBox('vol-b')
    const got = await sh(b, "if [ -e /data/message.txt ]; then cat /data/message.txt; else echo '<missing>'; fi")
    if (got === marker) {
      console.log("PASS: B read A's file after A was deleted")
    } else {
      console.log(`FAIL: B did not see A's data: ${JSON.stringify(got)}`)
      ok = false
    }
    const markerB = `hello-from-B-${RUN}`
    await sh(b, `echo ${markerB} >> /data/message.txt && sync`)
    console.log(`waiting ${SYNC_WAIT_SECONDS}s for B's changes to synchronize`)
    await sleep(SYNC_WAIT_SECONDS)
    await b.delete()
    boxes.splice(boxes.indexOf(b), 1)
    await sleep(3)

    console.log("3. sandbox C reads both writers' data")
    const c = await newBox('vol-c')
    const contents = await sh(c, 'if [ -e /data/message.txt ]; then cat /data/message.txt; fi')
    if (contents.includes(marker) && contents.includes(markerB)) {
      console.log('PASS: C sees changes from both A and B')
    } else {
      console.log(`FAIL: C is missing a writer's data: ${JSON.stringify(contents)}`)
      ok = false
    }
  } finally {
    if (!KEEP) {
      for (const box of boxes) {
        try {
          await box.delete()
        } catch (error) {
          if (!(error instanceof DaytonaError)) throw error
          console.log(`delete sandbox failed: ${error}`)
        }
      }
      // Allow sandbox deletion to finish before deleting the volume.
      await sleep(3)
      try {
        await daytona.volume.delete(volume)
      } catch (error) {
        if (!(error instanceof DaytonaError)) throw error
        console.log(`delete volume failed (retrying once): ${error}`)
        await sleep(5)
        await daytona.volume.delete(volume)
      }
    }
  }
  console.log(ok ? 'SUCCESS' : 'FAILURE')
  return ok ? 0 : 1
}

main().then((code) => process.exit(code)).catch((error: unknown) => {
  console.error(error)
  process.exit(1)
})
