/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { Receivers } from '../src/receivers.js';
import { loadMapping } from '../src/mapping.js';
import { callbackReceiverCases, objectReceiverCases } from './review-receiver-cases.js';

const sdk = "import { Daytona } from '@daytona/sdk';\nimport axios from 'axios';\n";

it.each(objectReceiverCases)('uses the final property without rewriting when $name', ({ object, review }) => {
  // Given valid JavaScript whose object writes may replace a Daytona initializer.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `${sdk}const deps = ${object}; deps.daytona.get();`);
  const receiver = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).find(node => node.getText() === 'deps.daytona');
  if (!receiver) throw new Error('Missing fixture receiver');
  const analyzer = new Receivers(loadMapping());
  // When the receiver is classified for a moved method.
  const actual = { rewrite: analyzer.client(receiver, 'get'), review: analyzer.plausible(receiver, 'get') };
  // Then only uncertain Daytona-bearing values need review, never a rewrite.
  expect(actual).toEqual({ rewrite: false, review });
});

it.each(callbackReceiverCases)('links only the matching callback argument when $name', ({ setup, use, review }) => {
  // Given a callback receiving an argument from its invoking helper.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `${sdk}${setup}\n${use}`);
  const access = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression).find(node => node.getText() === 'c.create');
  if (!access) throw new Error('Missing fixture callback');
  const analyzer = new Receivers(loadMapping());
  // When its untyped parameter is classified.
  const actual = { rewrite: analyzer.client(access.getExpression(), 'create'), review: analyzer.plausible(access.getExpression(), 'create') };
  // Then passed Daytona values link review evidence, not proof or unrelated arguments.
  expect(actual).toEqual({ rewrite: false, review });
});
