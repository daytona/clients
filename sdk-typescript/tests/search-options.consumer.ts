/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Strict-consumer fixture for the `SandboxSearchFilters` comma-list options.
// A consumer compiling with `strict: true` must be able to
// pass `status`, `sandboxClass` and `region` as a single value, a mutable array or a
// readonly (`as const`) array — with no cast anywhere in this file. It is
// type-checked by `npm run typecheck` (tsconfig.tests.json) and by ts-jest,
// and unit.test.ts executes `searchConsumerCalls` to pin the wire form.
import type { Daytona } from '../src/v2/Daytona'
import type { ListResult, ListSandboxesOptions, SearchSandboxesOptions } from '../src/v2/types'
import type { Sandbox } from '../src/v2/Sandbox'

/** The form the CHANGELOG advertises: one value per option. */
export const SINGLE_VALUE_SEARCH: SearchSandboxesOptions = { status: 'running', sandboxClass: 'linux', region: 'local' }

/** The filtered `list()` form with a list. */
export const LIST_VALUE_LIST: ListSandboxesOptions = { status: ['running', 'stopped'] }

const READONLY_REGIONS = ['region-a', 'region-b'] as const

export function searchConsumerCalls(client: Daytona): Promise<ListResult<Sandbox>[]> {
  return Promise.all([
    client.search({ status: 'running', sandboxClass: 'linux', region: 'local' }),
    client.list({ status: ['running', 'stopped'] }),
    client.search({ sandboxClass: ['linux', 'macos'], region: READONLY_REGIONS }),
  ])
}
