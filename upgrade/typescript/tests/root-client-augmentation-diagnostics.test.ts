/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['Daytona', 'DaytonaConfig'].flatMap(member => [
  `interface ${member} { custom(): void }`,
  `interface Sandbox { custom(): void } interface ${member} { custom(): void }`,
  `interface ${member} { custom(): void } interface DaytonaError { custom(): void }`,
]).flatMap(body => ['@daytonaio/sdk', '@daytonaio/sdk/dist/Daytona'].map(module => ({ body, module }))))
('requires review when $module augments a replaced client/config entity: $body', async ({ body, module }) => {
  // Given: no fake SDK exports that could incorrectly merge a Daytona augmentation.
  const declaration = `declare module '${module}' { ${body} }`;
  const root = temporaryProject({ 'app.ts': declaration });
  // When
  const result = await invoke(root, ['--json']);
  // Then: do not silently retarget an augmentation to a nonexistent v0 export.
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toBe(declaration);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'private-import', line: 1 }));
});
