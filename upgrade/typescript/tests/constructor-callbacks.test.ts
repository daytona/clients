/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { Receivers } from '../src/receivers.js';
import { constructorCallbackCases } from './constructor-callback-cases.js';

it.each(constructorCallbackCases)('requires only linked review when $name', ({ setup, use, review }) => {
  // Given an untyped receiver supplied through a constructor invocation.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';
import axios from 'axios';
${setup}
${use}`);
  const access = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).find(node => node.getText() === 'c.create');
  if (!access) throw new Error('Missing fixture receiver');
  const analyzer = new Receivers(loadMapping());
  // When the receiver is classified for migration.
  const actual = { rewrite: analyzer.client(access.getExpression(), 'create'), review: analyzer.plausible(access.getExpression(), 'create') };
  // Then only a matching Daytona argument requires review, never a rewrite.
  expect(actual).toEqual({ rewrite: false, review });
});
