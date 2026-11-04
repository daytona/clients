/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  '(client as any)',
  '(<any>client)',
  'client!',
  '(client satisfies Daytona)',
  '((client))',
  '((<Daytona>(client as Daytona))!)',
])('reports an unsafe fake when the rewritten consumer wraps its receiver as %s', async receiver => {
  // Given: a fake with an existing, incompatible v0 namespace cannot be adapted.
  const source = `import type { Daytona } from '@daytonaio/sdk';
function consume(client: Daytona) { return ${receiver}.create(); }
const fake = { create: () => 42, v0: {} } as any;
consume(fake);
`;
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': source,
  });
  installFakeSdk(root);
  const options = { compilerOptions: { module: ts.ModuleKind.CommonJS } };
  expect(runInNewContext(ts.transpileModule(source, options).outputText, { exports: {} })).toBe(42);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: the changed consumer breaks the fake, so strict mode must require review.
  const migrated = contents(root)['index.ts'] ?? '';
  expect(migrated).toContain(`${receiver}.v0.create()`);
  expect(() => runInNewContext(ts.transpileModule(migrated, options).outputText, { exports: {} })).toThrow();
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
});
