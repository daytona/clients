/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { Receivers } from '../src/receivers.js';
import { loadMapping } from '../src/mapping.js';
import { correlatedCallbackCases } from './correlated-callback-cases.js';
import { accessorReviewCases } from './accessor-review-cases.js';

it.each(correlatedCallbackCases)('correlates $name with its own invocation', ({ source: text, review }) => {
  // Given two independent invocations of the same wrapper.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', text);
  const access = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).find(node => node.getText() === 'c.get');
  if (!access) throw new Error('Missing fixture callback');
  const analyzer = new Receivers(loadMapping());
  // When the callback receiver is classified.
  const actual = { rewrite: analyzer.client(access.getExpression(), 'get'), review: analyzer.plausible(access.getExpression(), 'get') };
  // Then only this invocation's supplied value can link Daytona evidence.
  expect(actual).toEqual({ rewrite: false, review });
});

it.each(accessorReviewCases)('selects the getter result when $name', ({ object, review }) => {
  // Given ordered object definitions that can replace the original property.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';
import axios from 'axios';
const deps = ${object}; deps.daytona.get();`);
  const receiver = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).find(node => node.getText() === 'deps.daytona');
  if (!receiver) throw new Error('Missing fixture property');
  const analyzer = new Receivers(loadMapping());
  // When the effective read value is classified.
  const actual = { rewrite: analyzer.client(receiver, 'get'), review: analyzer.plausible(receiver, 'get') };
  // Then getter returns provide review evidence only, not replaced data values.
  expect(actual).toEqual({ rewrite: false, review });
});
