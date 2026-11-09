/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Runs under the fake SDK only (examples.test.ts): looks up an absent volume,
 * then writes to a volume, deletes it, creates it again under the same name
 * and reads it back. Prints one line per step.
 */

import { ConflictError, ImageBuilder, NotFoundError, type Sandbox } from '@daytona/sdk/v2'

import { client, IMAGE } from '../_common'

async function main(): Promise<void> {
  const daytona = client()
  const absent = await daytona.volumes.get('absent').then(() => 'found', (error: unknown) => error instanceof NotFoundError ? 'NotFoundError' : String(error))
  console.log(`get absent: ${absent}`)
  const read = "if [ -e /data/message.txt ]; then cat /data/message.txt; else echo '<missing>'; fi"
  let box: Sandbox | undefined
  const writeThenRead = async (volumeId: string): Promise<string> => {
    box = await daytona.createFromBuild(ImageBuilder.fromRegistry(IMAGE), { volumes: [{ volumeId, mountPath: '/data' }] })
    const before = await box.process.run(read)
    // The write must land, through the command and the fs alike.
    const written = await box.process.run('echo written > /data/message.txt')
    if (written.exitCode !== 0) throw new Error(`write failed: ${written.output}`)
    const back = await box.fs.readText('/data/message.txt')
    if (back !== 'written\n') throw new Error(`read back ${JSON.stringify(back)}`)
    await box.fs.writeText('/data/note.txt', 'note')
    const listed = (await box.fs.list('/data')).items.map((entry) => entry.name).sort()
    if (listed.join(',') !== 'message.txt,note.txt') throw new Error(`listed ${listed.join(',')}`)
    return before.output
  }
  const first = await daytona.volumes.create({ name: 'reused' })
  console.log(`first: ${await writeThenRead(first.id)}`)
  // A mounted volume cannot be deleted: delete its sandbox first, as the API requires.
  const inUse = await daytona.volumes.delete(first.id, { idOnly: true }).then(() => 'deleted', (error: unknown) => error instanceof ConflictError ? 'ConflictError' : String(error))
  console.log(`delete mounted: ${inUse}`)
  await box?.delete()
  await daytona.volumes.delete(first.id, { idOnly: true })
  const second = await daytona.volumes.create({ name: 'reused' })
  console.log(`recreated: ${await writeThenRead(second.id)}`)
  await box?.delete()
  await daytona.volumes.delete(second.id, { idOnly: true })
}

main().catch((error: unknown) => {
  console.error(error)
  process.exit(1)
})
