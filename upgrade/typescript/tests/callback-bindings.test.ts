/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { Receivers } from '../src/receivers.js';
import { callbackBindingCases } from './callback-binding-cases.js';

it.each(callbackBindingCases)('preserves review-only evidence when $name', ({ body, use, review }) => {
  // Given a local callback binding or a wrapper-written options property.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';
import axios from 'axios';
function ready(opts, value) { ${body} }
${use}`);
  const access = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).find(node => node.getText() === 'c.create');
  assert(access, 'Missing fixture callback receiver');
  const analyzer = new Receivers(loadMapping());
  // When the actual callback receiver is classified.
  const actual = { rewrite: analyzer.client(access.getExpression(), 'create'), review: analyzer.plausible(access.getExpression(), 'create') };
  // Then only linked Daytona arguments produce review, never a rewrite.
  expect(actual).toEqual({ rewrite: false, review });
});
