/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it('keeps SDK 2.x root imports at the root', async () => {
  // Given
  const code = "import { DaytonaV2Client, V0_API_URL } from '@daytona/sdk';\n";
  const root = temporaryProject({ 'index.ts': code });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(code);
});

it('moves the deprecated experimental fork method onto v0', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\nconst d = new Daytona(); d._experimental_fork('id');\n" });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain("d.v0._experimental_fork('id');");
});

it('keeps CommonJS rest bound to root exports when splitting resources', async () => {
  // Given
  const root = temporaryProject({ 'index.cjs': "const { Sandbox, ...rest } = require('@daytonaio/sdk');\nnew rest.Daytona() instanceof rest.Daytona;" });
  class Daytona {}
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(runInNewContext(contents(root)['index.cjs'] ?? '', { require: (name: string) =>
    name === '@daytona/sdk' ? { Daytona } : { Sandbox: class {} } })).toBe(true);
});

it('reports resource reads through a CommonJS root rest binding', async () => {
  // Given
  const root = temporaryProject({ 'index.cjs': "const { Sandbox, ...rest } = require('@daytonaio/sdk');\nrest.Volume;" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(contents(root)['index.cjs']).toContain('rest.Volume;');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]) });
});

it.each(['overrides', 'resolutions', 'pnpm'])('updates %s package constraints alongside dependencies', async section => {
  // Given
  const entries = { '@daytonaio/sdk': '0.224.0' };
  const root = temporaryProject({ 'package.json': JSON.stringify({ dependencies: entries,
    [section]: section === 'pnpm' ? { overrides: entries } : entries }) });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toEqual({ dependencies: { '@daytona/sdk': '^2.0.0' },
    [section]: section === 'pnpm' ? { overrides: { '@daytona/sdk': '^2.0.0' } } : { '@daytona/sdk': '^2.0.0' } });
});

it.each(['overrides', 'resolutions', 'pnpm'])('reports a non-string %s constraint without corrupting it', async section => {
  // Given
  const entries = { '@daytonaio/sdk': { '.': '0.224.0' } };
  const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' }, [section]: section === 'pnpm' ? { overrides: entries } : entries };
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest) });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toMatchObject({ [section]: manifest[section] });
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it('refuses a nonexistent project path as bad arguments', async () => {
  // Given
  const missing = join(temporaryProject(), 'missing');
  // When
  const result = await invoke(missing, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'bad-arguments', message: `Path does not exist: ${missing}` } });
});

it('refuses a file path and names the directory to pass instead', async () => {
  // Given
  const file = join(temporaryProject({ 'index.ts': "import { Daytona } from '@daytona/sdk';\n" }), 'index.ts');
  // When
  const result = await invoke(file, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'bad-arguments', message: expect.stringContaining('pass the directory that contains it') } });
});
