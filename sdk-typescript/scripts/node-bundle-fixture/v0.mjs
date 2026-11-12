/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// ESM entry touching d.v0: bundlers inline v0 through the literal require in
// loaders/v0-node.cjs (esbuild), so the bundle runs without node_modules.
import { Daytona } from '@daytona/sdk'

const d = new Daytona({ apiKey: 'k' })
console.log(typeof d.v0.list, d.v0 === d.v0)
