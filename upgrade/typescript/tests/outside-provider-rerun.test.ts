/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each(['ts', 'js'].flatMap(extension => [
  { extension, expression: 'client.create();' },
  { extension, expression: "client['create']();" },
  { extension, expression: "'create' in client;" },
]))('retains outside-provider review after installing SDK 2.x: $extension $expression', async ({ extension, expression }) => {
  // Given a completed consumer upgrade whose outside provider still imports SDK 0.x.
  const provider = "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();";
  const root = temporaryProject({
    'app/package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    [`outside/provider.${extension}`]: provider,
    [`app/barrel.${extension}`]: "export { client } from '../outside/provider.js';",
    [`app/use.${extension}`]: `import { client } from './barrel.js'; ${expression}`,
  });
  installFakeSdk(root);
  const app = join(root, 'app');
  const initial = await invoke(app, ['--strict', '--json']);
  expect(initial.code).toBe(1);
  const migrated = readFileSync(join(app, `use.${extension}`), 'utf8');
  expect(migrated).toContain('.v0');
  const sdk = join(app, 'node_modules/@daytona/sdk');
  mkdirSync(sdk, { recursive: true });
  writeFileSync(join(sdk, 'package.json'), '{"name":"@daytona/sdk","version":"2.0.0","types":"index.d.ts"}');
  writeFileSync(join(sdk, 'index.d.ts'), 'export declare class Daytona { readonly v0: { create(): void }; }');

  // When strict mode checks the already-migrated consumer with its newly installed SDK.
  const result = await invoke(app, ['--strict', '--json']);

  // Then the unmigrated provider still counts once, without changing either source.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([expect.objectContaining({
    file: `use.${extension}`,
    message: `use.${extension} uses a Daytona client from ../outside/provider.${extension}, outside the scanned folder; migrate that file too.`,
  })]);
  expect(result.code).toBe(1);
  expect(readFileSync(join(app, `use.${extension}`), 'utf8')).toBe(migrated);
  expect(readFileSync(join(root, `outside/provider.${extension}`), 'utf8')).toBe(provider);
});

it.each([
  { path: 'app/provider.ts', source: "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();", imported: './provider' },
  { path: 'outside/provider.ts', source: 'export const client = { v0: { create() {} } };', imported: '../outside/provider' },
])('keeps existing v0 access clean without an outside SDK provider: $path', async ({ path, source, imported }) => {
  // Given an existing v0 access backed by a scanned client or unrelated outside value.
  const consumer = `import { client } from '${imported}'; client.v0.create();`;
  const root = temporaryProject({
    'app/package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    [path]: source,
    'app/use.ts': consumer,
  });
  installFakeSdk(root);

  // When strict mode checks the consumer.
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);

  // Then neither provider creates a manual item or a redundant rewrite.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, 'app/use.ts'), 'utf8')).toBe(consumer);
});
