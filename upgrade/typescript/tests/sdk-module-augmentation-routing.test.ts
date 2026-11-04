/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { member: 'Sandbox', target: '@daytona/sdk/v0' },
  { member: 'DaytonaError', target: '@daytona/sdk/v0' },
])('routes an augmentation of $member to $target', async ({ member, target }) => {
  // Given
  const root = temporaryProject({ 'app.ts': `import '@daytonaio/sdk';\ndeclare module '@daytonaio/sdk' { interface ${member} { custom(): void } }\n` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain(`declare module '${target}'`);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]));
});

it.each([
  "interface Daytona { custom(): void } interface DaytonaError { custom(): void }",
  'interface Unknown { custom(): void }',
  '',
  'export { Daytona as Client };',
])('reports unclear or mixed augmentation without rewriting it: %s', async body => {
  // Given
  const declaration = `declare module '@daytonaio/sdk' { ${body} }`;
  const root = temporaryProject({ 'app.ts': declaration });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toBe(declaration);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import', line: 1 })]));
});

it('reports a private SDK client augmentation for review', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': "declare module '@daytonaio/sdk/dist/Daytona' { interface Daytona { custom(): void } }" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain("declare module '@daytonaio/sdk/dist/Daytona'");
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'private-import' }));
});
