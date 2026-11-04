/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { applyEdits } from '../src/edits.js';
import { imports } from '../src/imports.js';
import { loadMapping } from '../src/mapping.js';

it('mentions the adjacent package string when rewriting an optional peer import', () => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', "loadOptionalPeer(() => import('@daytonaio/sdk'), { packageName: '@daytonaio/sdk' });");
  // When
  const result = imports(source, loadMapping(), '/');
  // Then: flag the metadata, but never rewrite arbitrary strings.
  expect(applyEdits(source.getFullText(), result.edits)).toBe("loadOptionalPeer(() => import('@daytona/sdk'), { packageName: '@daytonaio/sdk' });");
  expect(result.items).toMatchObject([{ kind: 'dynamic-import', message: "Review dynamic import: SDK 2.x client methods live on .v0; resource exports now live in @daytona/sdk/v0; also update the adjacent '@daytonaio/sdk' string" }]);
});

it.each([
  "loadOptionalPeer(() => import('@daytonaio/sdk'), { packageName: '@daytonaio/sdk/other' });",
  "loadOptionalPeer(() => import('@daytonaio/sdk')); unrelated({ packageName: '@daytonaio/sdk' });",
])('keeps unrelated strings out of the dynamic-import item when given %s', input => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/index.ts', input);
  // When
  const result = imports(source, loadMapping(), '/');
  // Then
  expect(result.items).toMatchObject([{ kind: 'dynamic-import', message: 'Review dynamic import: SDK 2.x client methods live on .v0; resource exports now live in @daytona/sdk/v0.' }]);
});
