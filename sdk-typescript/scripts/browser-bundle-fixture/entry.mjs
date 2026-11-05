/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Bundled by the tarball smoke for the browser: an import of @daytona/sdk/v2
// alone (its builders, types and errors) must bundle without any v0 code. The
// v2 client itself comes only from the root.
import { ImageBuilder, NotFoundError } from '@daytona/sdk/v2'

export const sdk = [ImageBuilder, NotFoundError]
