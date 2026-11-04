/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { execute } from './client-dispatch-runtime.js';

it.each(['new FakeDaytona()', 'fake', 'alias'])('reports the argument when %s overrides the consumer member', async argument => {
  // Given: the override, including a call inside it, must remain unchanged.
  const subclass = 'class FakeDaytona extends Daytona { create() { return 42; } local() { return this.create(); } }';
  const source = `import { Daytona } from '@daytonaio/sdk';\n${subclass}\nfunction consume(client: Daytona) { return client.create(); }\nconst fake: Daytona = new FakeDaytona(); const alias = fake;\nconsume(${argument});\n`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'tsconfig.json': '{"compilerOptions":{"strict":true}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: routing to real v0 is explicitly reported at the argument, not silently accepted.
  const migrated = contents(root)['index.ts'] ?? '';
  expect(migrated).toContain(subclass);
  expect(execute(migrated)).toBe(99);
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', line: 5, column: 9 }));
});

it('accepts a subclass when its override is unrelated to the moved consumer member', async () => {
  // Given
  const source = "import { Daytona } from '@daytonaio/sdk'; class FakeDaytona extends Daytona { create() { return 42; } } function consume(client: Daytona) { return client.get('id'); } consume(new FakeDaytona());";
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  expect(execute(source)).toBe(11);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(execute(contents(root)['index.ts'] ?? '')).toBe(23);
  expect(result.code, result.output).toBe(0);
});
