/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { badRequest, headerText } from './options.js'
import { isBlank, strip } from './pyText.js'

/**
 * The region `create*()`, `createFromBuild()`, `findByName()` and
 * `images.build()` / `pull()` select, as Python's `region_selector` (the
 * reservation calls send `region` as given, since their contract reads a blank
 * value or `local` as no region; the search and Mac-catalog filters list
 * regions, where `local` has no meaning): `undefined` / `null`, a blank or
 * whitespace-only string, or `local` in any letter case and with any
 * surrounding whitespace select the default region and are not sent (no
 * `x-daytona-region` header, no `region_id` field); any other string is sent
 * as given and must follow the header rule (`headerText`); a non-string is
 * `BadRequestError`.
 * @internal
 */
export function selectRegion(region: unknown): string | undefined {
  if (region === undefined || region === null) return undefined
  if (typeof region !== 'string') throw badRequest('region must be a string')
  if (isBlank(region) || strip(region).toLowerCase() === 'local') return undefined
  return headerText(region, 'region')
}
