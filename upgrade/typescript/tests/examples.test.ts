/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { Project, SyntaxKind } from 'ts-morph';
import { Receivers } from '../src/receivers.js';
import { loadMapping } from '../src/mapping.js';
import { contents, here, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { testTimeout } from './budget.js';

const examplesRoot = process.env['DAYTONA_UPGRADE_V0_EXAMPLES'];
const examples = join(examplesRoot ?? '', 'typescript');
it.skipIf(!examplesRoot)('upgrades pinned examples without exceptions or remaining proven root calls', async () => {
  // Given
  const root = temporaryProject();
  cpSync(examples, root, { recursive: true, filter: path => !path.split('/').includes('node_modules') });
  installFakeSdk(root);
  mkdirSync(join(root, 'node_modules/@daytona/sdk'), { recursive: true });
  cpSync(join(here, 'fake-sdk'), join(root, 'node_modules/@daytona/sdk'), { recursive: true });
  // Real examples may have non-literal Cloud URLs: the assertion is explicit.
  // When
  const result = await invoke(root, ['--drop-api-url', '--json']);
  // Then
  expect(result.errors).toBe('');
  expect(result.code).toBe(0);
  const project = new Project({ compilerOptions: { allowJs: true } });
  project.addSourceFilesAtPaths([join(root, '**/*.ts'), `!${join(root, 'node_modules/**')}`]);
  const mapping = loadMapping();
  const receivers = new Receivers(mapping);
  for (const source of project.getSourceFiles().filter(source => !source.getFilePath().includes('/node_modules/'))) {
    for (const access of source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression)) {
      if (mapping.movedClientMembers.ts.includes(access.getName())) expect(receivers.client(access.getExpression(), access.getName())).toBe(false);
    }
  }
}, testTimeout(120_000));
it.skipIf(!examplesRoot)('leaves examples unchanged when run on upgraded output', async () => {
  // Given
  const root = temporaryProject();
  cpSync(examples, root, { recursive: true, filter: path => !path.split('/').includes('node_modules') });
  await invoke(root, ['--drop-api-url']);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--drop-api-url']);
  // Then
  expect(result.errors).toBe('');
  expect(contents(root)).toEqual(before);
}, testTimeout(120_000));
