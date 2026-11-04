/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { upgrade } from '../src/upgrade.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const imports = "import { Daytona, DaytonaError } from '@daytonaio/sdk';\n";
const protectedBodies = [
  'const c = new Daytona(); return await c.list();',
  'return await new Daytona().list();',
  'const c = new Daytona(); use(c.v0);',
  'const c = new Daytona(); use(c["v0"]);',
  'const c = new Daytona(); return c.snapshot.list();',
] as const;

it.each(protectedBodies)('keeps strict mode clean when the try evaluates v0: %s', async body => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'app.ts': `${imports}async function run() { try { ${body} } catch (e) { if (e instanceof DaytonaError) recover(); } }`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.errors).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['app.ts']).not.toContain('FIXME(daytona-upgrade)');
  expect(contents(root)['app.ts']).toMatch(/\.v0|\["v0"\]/);
});

it.each([
  'const c = new Daytona(); const later = () => c.v0;',
  'const c = new Daytona(); const later = () => c.list();',
  'const c = new Daytona(); { const c = other; use(c.v0); }',
  'const c = new Daytona(); const other = new Daytona(); use(other.v0);',
  'const c = new Daytona(); } catch {} use(c.v0); try { work();',
])('retains the constructor item when its client is not evaluated in the try: %s', body => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${imports}try { ${body} } catch {}`);
  // When
  const result = upgrade(source, loadMapping(), { root: '/', dropApiUrl: false });
  // Then
  expect(result.items.filter(item => item.message.startsWith('SDK 2.x builds'))).toHaveLength(1);
});

it('uses anonymous guidance when a construction is passed as an argument', () => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${imports}try { register(new Daytona()); } catch {}`);
  // When
  const result = upgrade(source, loadMapping(), { root: '/', dropApiUrl: false });
  // Then
  expect(result.items[0]?.message).toContain('access .v0 on this client inside the try');
});
