/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Runs under the fake SDK only (examples.test.ts): looks up an absent volume,
 * then writes to a volume, deletes it, creates it again under the same name
 * and reads it back. Prints one line per step.
 */

import { ImageBuilder, NotFoundError } from '@daytona/sdk/v2'

import { client, IMAGE } from '../_common'

async function main(): Promise<void> {
  const daytona = client()
  const absent = await daytona.volumes.get('absent').then(() => 'found', (error: unknown) => error instanceof NotFoundError ? 'NotFoundError' : String(error))
  console.log(`get absent: ${absent}`)
  const read = "if [ -e /data/message.txt ]; then cat /data/message.txt; else echo '<missing>'; fi"
  const writeThenRead = async (volumeId: string): Promise<string> => {
    const box = await daytona.createFromBuild(ImageBuilder.fromRegistry(IMAGE), { volumes: [{ volumeId, mountPath: '/data' }] })
    const before = await box.process.run(read)
    await box.process.run('echo written > /data/message.txt')
    return before.output
  }
  const first = await daytona.volumes.create({ name: 'reused' })
  console.log(`first: ${await writeThenRead(first.id)}`)
  await daytona.volumes.delete(first.id, { idOnly: true })
  const second = await daytona.volumes.create({ name: 'reused' })
  console.log(`recreated: ${await writeThenRead(second.id)}`)
}

main().catch((error: unknown) => {
  console.error(error)
  process.exit(1)
})
