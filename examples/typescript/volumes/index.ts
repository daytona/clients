/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Mount one volume into multiple sandboxes, including a volume subpath.
 * Run from examples with DAYTONA_API_KEY set:
 *   npx tsx typescript/volumes/index.ts
 *
 * Writes become available to other sandboxes asynchronously. See
 * volumes_share.ts for a persistence round-trip with synchronization waits.
 */

import { DaytonaError } from '@daytona/sdk'
import { ImageBuilder } from '@daytona/sdk/v2'
import type { Sandbox } from '@daytona/sdk/v2'
import { assertStarted, client, section, info, IMAGE, RUN } from '../_common'

async function main(): Promise<void> {
  const daytona = client()

  section('volumes.get(name, { create: true })')
  const volume = await daytona.volumes.get(`my-volume-${RUN}`, { create: true })
  info(`volume id=${volume.id} name=${volume.name} status=${volume.status}`)

  const boxes: Sandbox[] = []
  try {
    section('sandbox 1: mount volume at /mnt/vol and write a file')
    const mount1 = '/mnt/vol'
    const s1 = await daytona.createFromBuild(ImageBuilder.fromRegistry(IMAGE), { name: `vol1-${RUN}`, volumes: [{ volumeId: volume.id, mountPath: mount1 }], buildTimeoutSeconds: 240, waitSeconds: 240 })
    boxes.push(s1)
    assertStarted(s1)
    await s1.fs.mkdir(`${mount1}/new-dir`, { mode: '755' })
    await s1.fs.writeText(`${mount1}/new-file.txt`, 'Hello, World!')
    info(`s1 wrote ${mount1}/new-file.txt`)

    section('sandbox 2: mount the same volume at a different path')
    const mount2 = '/mnt/other'
    const s2 = await daytona.createFromBuild(ImageBuilder.fromRegistry(IMAGE), { name: `vol2-${RUN}`, volumes: [{ volumeId: volume.id, mountPath: mount2 }], buildTimeoutSeconds: 240, waitSeconds: 240 })
    boxes.push(s2)
    assertStarted(s2)
    const files = (await s2.fs.list(mount2)).items
    info(`s2 sees at ${mount2}: ${files.map((f) => f.name).join(', ') || '(not yet synchronized)'}`)

    section('sandbox 3: mount a subpath of the volume')
    const mount3 = '/mnt/alice'
    const s3 = await daytona.createFromBuild(ImageBuilder.fromRegistry(IMAGE), {
      name: `vol3-${RUN}`,
      volumes: [{ volumeId: volume.id, mountPath: mount3, subpath: 'users/alice' }],
      buildTimeoutSeconds: 240,
      waitSeconds: 240,
    })
    boxes.push(s3)
    assertStarted(s3)
    await s3.fs.writeText(`${mount3}/alice-file.txt`, "Hello from Alice's subpath!")
    info(`s3 wrote ${mount3}/alice-file.txt (volume-root/users/alice/alice-file.txt)`)
  } finally {
    section('cleanup')
    for (const box of boxes) {
      try {
        await daytona.delete(box)
        info(`deleted sandbox ${box.id}`)
      } catch (error) {
        if (!(error instanceof DaytonaError)) throw error
        info(`delete sandbox failed: ${error}`)
      }
    }
    try {
      await daytona.volumes.delete(volume.id, { idOnly: true })
      info(`deleted volume ${volume.id}`)
    } catch (error) {
      if (!(error instanceof DaytonaError)) throw error
      info(`volume delete failed (delete its sandboxes first): ${error}`)
    }
  }
}

main().catch((error: unknown) => {
  console.error('Error:', error)
  process.exit(1)
})
