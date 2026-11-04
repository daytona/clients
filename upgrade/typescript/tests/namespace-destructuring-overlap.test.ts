/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  'export const tools = sdk;',
  'export { sdk };',
  'export { sdk as tools };',
  'export default sdk;',
  'const tools = sdk; export { tools };',
])('reports a namespace exported from its importing module: %s', async code => {
  // Given
  const root = temporaryProject({ 'state.ts': `import * as sdk from '@daytonaio/sdk'; ${code}` });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]));
});

it('reports namespace destructuring defaults when splitting would overlap a resource edit', async () => {
  // Given
  const code = 'const { Image: Img = sdk.Image, Daytona } = sdk;';
  const root = temporaryProject({ 'index.ts': `import * as sdk from '@daytonaio/sdk'; ${code}` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]));
  expect(contents(root)['index.ts']).toContain("from '@daytona/sdk'");
  expect(contents(root)['index.ts']).toContain(code);
});

it('keeps rewriting namespace destructuring when a root-only default cannot overlap', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import * as sdk from '@daytonaio/sdk'; const { Image: Img, Daytona: D = sdk.Daytona } = sdk;" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('{ Daytona: D = sdk.Daytona } = sdk, { Image: Img } = sdkV0');
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]));
});
