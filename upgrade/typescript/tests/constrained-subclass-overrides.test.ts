/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const cases = [
  { parameter: 'LocalDaytona', generic: '', override: true, rewrite: false },
  { parameter: 'T', generic: 'T extends LocalDaytona', override: true, rewrite: false },
  { parameter: 'T', generic: 'T extends U, U extends LocalDaytona', override: true, rewrite: false },
  { parameter: 'T', generic: 'T extends LocalDaytona & { tag: string }', override: true, rewrite: false },
  { parameter: 'T', generic: 'T extends U & { tag: string }, U extends LocalDaytona', override: true, rewrite: false },
  { parameter: 'T', generic: 'T extends LocalDaytona', override: false, rewrite: true },
  { parameter: 'T', generic: 'T extends LocalDaytona & { tag: string }', override: false, rewrite: true },
  { parameter: 'T', generic: 'T extends Daytona', override: false, rewrite: true },
  { parameter: 'T', generic: 'T', override: false, rewrite: false },
] as const;

it.each(cases)('preserves dispatch when <$generic> override=$override', async ({ parameter, generic, override, rewrite }) => {
  // Given: the installed SDK owns create unless a project subclass overrides it.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'tsconfig.json': '{"compilerOptions":{"strict":true,"skipLibCheck":true,"noEmit":true}}',
    'app.ts': `import { Daytona } from '@daytonaio/sdk';
class LocalDaytona extends Daytona { ${override ? 'async create(): Promise<never> { throw new Error("local override"); }' : ''} }
async function run${generic ? `<${generic}>` : ''}(client: ${parameter}) { return client.create(); }
`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then: rewriting an override would bypass the user's implementation.
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toContain(rewrite ? 'return client.v0.create();' : 'return client.create();');
});
