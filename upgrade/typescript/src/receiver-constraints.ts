/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Type } from 'ts-morph';

/** Expand generic constraints without losing local members to SDK base types. */
export function receiverConstraints(value: Type, seen: ReadonlySet<Type> = new Set()): readonly Type[] {
  const type = value.getNonNullableType();
  if (seen.has(type)) return [];
  const path = new Set(seen).add(type);
  if (type.isTypeParameter()) {
    const constraint = type.getConstraint();
    return constraint ? receiverConstraints(constraint, path) : [type];
  }
  return type.isIntersection() ? type.getIntersectionTypes().flatMap(part => receiverConstraints(part, path)) : [type];
}
