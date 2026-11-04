/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// A static @daytona/sdk/v0 import (its types and errors) next to d.v0: one
// module, so v0's errors and d.v0 come from the same copy of the v0 SDK.
import { Daytona } from '@daytona/sdk'
import * as v0 from '@daytona/sdk/v0'

const d = new Daytona({ apiKey: 'k' })
console.log(typeof d.v0.list, d.v0 instanceof v0.RootOnlyClientV0)
