/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { loadMapping, parseMapping } from '../src/mapping.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  "import * as sdk from '@daytonaio/sdk';",
  "const sdk = require('@daytonaio/sdk');",
])('moves destructured resources when the namespace is %s', async declaration => {
  // Given
  const root = temporaryProject({ 'index.ts': `${declaration}\nconst { Daytona: D, Image: I } = sdk;\nnew D().create({ image: I.debianSlim() });\n` });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('{ Daytona: D } = sdk, { Image: I } = sdkV0');
  expect(contents(root)['index.ts']).toContain('new D().v0.create');
});

it.each(['const', 'let'])('follows never-reassigned %s namespace aliases', async keyword => {
  // Given
  const root = temporaryProject({ 'index.ts': `import * as sdk from '@daytonaio/sdk';\n${keyword} tools = sdk;\nconst nested = tools;\nnested.Image.debianSlim();\nnew nested.Daytona().create();\n` });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain('sdkV0.Image.debianSlim()');
  expect(contents(root)['index.ts']).toContain('new nested.Daytona().v0.create()');
});

it.each([
  'let tools = sdk; tools = other; tools.Image;',
  'sdk[key];',
  'consume(sdk);',
  'const { ...rest } = sdk; rest.Image;',
])('reports a manual item when the namespace escapes: %s', async body => {
  // Given
  const root = temporaryProject({ 'index.ts': `import * as sdk from '@daytonaio/sdk';\n${body}\n` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]) });
});

it.each([
  "import type { SandboxCodeToolbox } from '@daytonaio/sdk';",
  "export { SandboxCodeToolbox } from '@daytonaio/sdk';",
  "const { SandboxCodeToolbox } = require('@daytonaio/sdk');",
  "import * as sdk from '@daytonaio/sdk'; sdk.SandboxCodeToolbox;",
  "import * as sdk from '@daytonaio/sdk'; const { SandboxCodeToolbox } = sdk;",
])('reports a missing pinned export when moving %s', async code => {
  // Given
  const root = temporaryProject({ 'index.ts': code });
  const mapping = { ...loadMapping(), v0Exports: { ts: ['Image'] } };
  // When
  const result = await invoke(root, ['--json'], mapping);
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'private-import', message: expect.stringContaining('SandboxCodeToolbox') })]) });
});

it('skips the pinned-export check when only another language has an export list', () => {
  // Given
  const raw = { ...loadMapping(), v0Exports: { py: ['Image'] } };
  // When
  const mapping = parseMapping(raw);
  // Then
  expect(mapping.v0Exports).toBeUndefined();
});

it('loads the optional pinned TypeScript export list', () => {
  // Given
  const raw = { ...loadMapping(), v0Exports: { ts: ['Image'] } };
  // When
  const mapping = parseMapping(raw);
  // Then
  expect(mapping.v0Exports?.ts).toEqual(['Image']);
});
