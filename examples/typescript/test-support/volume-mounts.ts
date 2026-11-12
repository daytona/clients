/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Runs under the fake SDK only (examples.test.ts): three sandboxes mount one
 * volume, the first at subpath `a`, the second at subpath `b`, the third at
 * `a` read-only, and each reads or writes /data/message.txt. Prints one line
 * per step.
 */

import { ImageBuilder, type VolumeMount } from '@daytona/sdk/v2'

import { client, IMAGE, RUN } from '../_common'

async function main(): Promise<void> {
  const daytona = client()
  const volume = await daytona.volumes.create({ name: `mounts-${RUN}` })
  const box = (mount: Omit<VolumeMount, 'volumeId' | 'mountPath'>) =>
    daytona.createFromBuild(ImageBuilder.fromRegistry(IMAGE), { volumes: [{ volumeId: volume.id, mountPath: '/data', ...mount }] })
  const show = async (label: string, sandbox: Awaited<ReturnType<typeof box>>, command: string): Promise<void> => {
    const result = await sandbox.process.run(command)
    console.log(`${label}: exit=${result.exitCode} ${result.output}`)
  }
  const writer = await box({ subpath: 'a' })
  await show('write a', writer, 'echo from-a > /data/message.txt')
  await show('read b', await box({ subpath: 'b' }), "if [ -e /data/message.txt ]; then cat /data/message.txt; else echo '<missing>'; fi")
  const reader = await box({ subpath: 'a', readOnly: true })
  await show('read a read-only', reader, 'cat /data/message.txt')
  await show('write a read-only', reader, 'echo again > /data/message.txt')
}

main().catch((error: unknown) => {
  console.error(error)
  process.exit(1)
})
