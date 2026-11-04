/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { assertNever } from './model.js';

export type Provenance = 'daytona' | 'not-daytona' | 'unknown';
// Undefined is retained only to select the value branch of lazy caches.
export type Origin = Provenance | 'undefined' | 'incomplete';

export function provenance(origin: Origin): Provenance {
  switch (origin) {
    case 'undefined': case 'incomplete': return 'unknown';
    case 'daytona': case 'not-daytona': case 'unknown': return origin;
    default: return assertNever(origin);
  }
}

export function combined(values: readonly Origin[]): Origin {
  if (values.includes('incomplete')) return 'incomplete';
  if (values.length === 0) return 'not-daytona';
  if (values.every(value => value === 'not-daytona')) return 'not-daytona';
  if (values.every(value => value === 'undefined')) return 'undefined';
  return values.every(value => value === 'daytona') ? 'daytona' : 'unknown';
}
