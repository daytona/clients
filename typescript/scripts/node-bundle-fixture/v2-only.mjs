/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Bundled by the tarball smoke with `esbuild --platform=node --format=cjs`
// (the AWS CDK NodejsFunction default): importing the root and using only v2
// must not crash at import time.
import { Daytona } from '@daytona/sdk'

const d = new Daytona({ apiKey: 'k' })
console.log(typeof d.v2.list)
