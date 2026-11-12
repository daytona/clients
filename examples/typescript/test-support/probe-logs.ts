/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Prints the log frames of one command run through the test double, for
// examples.test.ts: a run's output is also its log, as in the SDK.

import { Daytona } from '@daytona/sdk'

const image = process.env.DAYTONA_IMAGE ?? ''
const sandbox = await new Daytona().v2.create(image)
const result = await sandbox.process.run('git -C /repo log --oneline -1')
console.log(`LOGS ${JSON.stringify(result.logs.map((frame) => [frame.channel, frame.data]))}`)
