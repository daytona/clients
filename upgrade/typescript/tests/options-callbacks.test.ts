/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { Receivers } from '../src/receivers.js';
import { optionsCallbackCases } from './options-callback-cases.js';

it.each(optionsCallbackCases)('requires only linked review when $name', ({ setup, use, review }) => {
  // Given an options callback and the values forwarded by its own invocation.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';
import axios from 'axios';
${setup}
${use}`);
  const access = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).find(node => node.getText() === 'c.create');
  assert(access, 'Missing fixture callback receiver');
  const analyzer = new Receivers(loadMapping());
  // When the callback receiver is classified for migration.
  const actual = { rewrite: analyzer.client(access.getExpression(), 'create'), review: analyzer.plausible(access.getExpression(), 'create') };
  // Then matching Daytona provenance requires review, never an automatic rewrite.
  expect(actual).toEqual({ rewrite: false, review });
});
