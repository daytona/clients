/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Runs under the fake SDK only (examples.test.ts): reads a registering image
 * without and then with `wait: 'ready'`, printing its status each time.
 */

import { client, IMAGE_NAME } from '../_common'

async function main(): Promise<void> {
  const daytona = client()
  for await (const image of daytona.images.iter({ name: IMAGE_NAME })) {
    console.log(`get: ${(await daytona.images.get(image.id)).status}`)
    console.log(`get ready: ${(await daytona.images.get(image.id, { wait: 'ready', timeoutSeconds: 5 })).status}`)
  }
}

main().catch((error: unknown) => {
  console.error(error)
  process.exit(1)
})
