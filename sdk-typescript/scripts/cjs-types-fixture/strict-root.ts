/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Typechecked with skipLibCheck: false (strict consumers): importing only the
// root must not fail on the package's own declarations (#v0-loader, ws types).
import { Daytona } from '@daytona/sdk'

export const client = new Daytona({ apiKey: 'smoke' })
