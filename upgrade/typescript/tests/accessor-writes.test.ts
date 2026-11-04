/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { ambiguousObjectProperty } from '../src/object-member-proof.js';
import { accessorWriteCases } from './accessor-write-cases.js';

it.each(accessorWriteCases)('preserves accessor ambiguity when $name', ({ name, write }) => {
  // Given a getter/setter pair and a potentially replacing property write.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `const other = {}; const replacement = { client: other };
const deps = { get client() { return client; }, set client(v) {}, other: null };
${write}
deps.client.create();`);
  const getter = source.getDescendantsOfKind(SyntaxKind.GetAccessor)[0];
  if (!getter) throw new Error('Missing fixture getter');
  // When the getter's receiver proof is checked.
  const ambiguous = ambiguousObjectProperty(getter);
  // Then only an unwritten pair retains its single-property proof.
  expect(ambiguous).toBe(write !== '' && name !== 'unrelated property write');
});
