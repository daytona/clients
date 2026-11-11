/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['overrides', 'resolutions', 'pnpm'].flatMap(section => ['@daytonaio/sdk', '@daytona/sdk'].map(name => ({ section, name }))))('reports a qualified $section selector for $name', async ({ section, name }) => {
  // Given
  const constraints = { [`${name}@^0.224.0`]: '0.224.0' };
  const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' },
    [section]: section === 'pnpm' ? { overrides: constraints } : constraints };
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest) });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toMatchObject({ [section]: manifest[section] });
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it.each(['overrides', 'resolutions', 'pnpm'].flatMap(section => [
  'npm:@custom/sdk@0.224.0', 'git+https://example.com/custom.git#v0.224.0',
  'file:../sdk', 'https://example.com/sdk.tgz', 'github:custom/sdk', '$@daytonaio/sdk',
].map(spec => ({ section, spec }))))('preserves a custom $section source: $spec', async ({ section, spec }) => {
  // Given
  const constraints = { '@daytonaio/sdk': spec };
  const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' },
    [section]: section === 'pnpm' ? { overrides: constraints } : constraints };
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest) });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toMatchObject({ [section]: manifest[section] });
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it.each([
  { dependencies: { '@daytonaio/sdk': '^0.200.0', '@daytona/sdk': '^2.0.0' }, resolutions: { '@daytonaio/sdk': '0.200.1' } },
  { dependencies: { '@daytonaio/sdk': '^0.200.0' }, resolutions: { '@daytonaio/sdk': '0.200.1', '@daytona/sdk': '2.0.0' } },
])('leaves every old-name entry when both package names are present: %j', async manifest => {
  // Given
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest), 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" });
  // When
  const result = await invoke(root, ['--json']);
  // Then: the dependency and its resolution keep naming the same package.
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toEqual(manifest);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({
    kind: 'config-field', message: 'Both Daytona package names are present; reconcile the duplicate dependency manually.' })]) });
});

it('migrates the old dependency when a constraint section names only the new package', async () => {
  // Given
  const manifest = { dependencies: { '@daytonaio/sdk': '^0.200.0' }, resolutions: { '@daytona/sdk': '2.0.0' } };
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest), 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" });
  // When
  const result = await invoke(root, ['--json']);
  // Then: no duplicate-name item, and the dependency moves to the new package.
  expect(result.output).not.toContain('Both Daytona package names are present');
  expect(Object.keys(JSON.parse(contents(root)['package.json'] ?? '').dependencies)).toEqual(['@daytona/sdk']);
});

it('migrates old-name constraints when only the new package is a dependency', async () => {
  // Given
  const manifest = { dependencies: { '@daytona/sdk': '^2.0.0' }, resolutions: { '@daytonaio/sdk': '0.200.1' } };
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest), 'index.ts': "import { Daytona } from '@daytona/sdk';\n" });
  // When
  const result = await invoke(root, ['--json']);
  // Then: nothing is duplicated, so the constraint follows the rename.
  expect(result.output).not.toContain('Both Daytona package names are present');
  expect(Object.keys(JSON.parse(contents(root)['package.json'] ?? '').resolutions)).toEqual(['@daytona/sdk']);
});
