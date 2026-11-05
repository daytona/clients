/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Bundled by the tarball smoke for the browser (esbuild and webpack --target
// web): an app importing the ROOT gets the v0 SDK statically through the
// root's browser `#v0-loader`, so `daytona.v0` works synchronously. The smoke
// runs the bundle and prints what it prints.
import { Daytona } from '@daytona/sdk'

const d = new Daytona({ apiKey: 'k' })
console.log(typeof d.v0.list, d.v0 === d.v0, typeof d.v2.list)
