/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { bindingAssignments } from '../src/binding-assignments.js';

it.each([
  { code: 'fn = other;', count: 1 },
  { code: 'fn ||= other;', count: 1 },
  { code: '({ fn } = other);', count: 1 },
  { code: '({ key: fn = fallback } = other);', count: 2 },
  { code: '[fn, ...fn] = other;', count: 1 },
  { code: 'for (fn of values) {}', count: 1 },
  { code: 'for (fn in values) {}', count: 1 },
  { code: 'fn.property = other;', count: 0 },
  { code: '({ fn: other = fn } = value);', count: 0 },
])('preserves assignment-target semantics when indexing $code', ({ code, count }) => {
  // Given an actual function binding and a source containing a write/read shape.
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('app.ts', `function fn() {} ${code}`);
  const binding = source.getFunctionOrThrow('fn');
  // When multiple queries share the source snapshot.
  const results = [bindingAssignments(binding), bindingAssignments(binding)];
  // Then each query reports the same writes, never reads or duplicate targets.
  expect(results.map(result => result.length)).toEqual([count, count]);
});

it('rebuilds assignment evidence when a source snapshot changes', () => {
  // Given an already queried project before a new assignment is inserted.
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('app.ts', 'function fn() {}');
  const binding = source.getFunctionOrThrow('fn');
  bindingAssignments(binding);
  source.addStatements('fn = other;');
  // When
  const writes = bindingAssignments(binding);
  // Then stale cached proof cannot conceal the new write.
  expect(writes.map(write => write.getText())).toEqual(['fn = other']);
});
