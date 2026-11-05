/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// CommonJS entry touching d.v0 (the CJS root's loader).
const { Daytona } = require('@daytona/sdk')

const d = new Daytona({ apiKey: 'k' })
console.log(typeof d.v0.list, d.v0 === d.v0)
