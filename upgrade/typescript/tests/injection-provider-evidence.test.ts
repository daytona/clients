/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each(["buildClient('daytona')", "createDaytonaThing('x')"])(
  'keeps unrelated factories clean when their name or argument mentions Daytona: %s', async receiver => {
    // Given local factories that provably return unrelated objects, not DI lookups.
    const source = `function buildClient(key) { return { create() {} }; }
      function createDaytonaThing(key) { return { create() {} }; }
      ${receiver}.create();`;
    const root = temporaryProject({ 'package.json': manifest,
      'main.js': "import { Daytona } from '@daytonaio/sdk';", 'use.js': source });
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then normal provenance analysis wins over incidental string/name matches.
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toEqual([]);
    expect(contents(root)['use.js']).toBe(source);
    expect(result.code).toBe(0);
  });

it.each(['get', 'resolve', 'inject', 'use', 'make', 'getService', 'require'].flatMap(name => [
  `container.${name}('DaYtOnA')`, `container['${name}']('daytona')`,
]).concat(["locals['daytona']", 'ctx.daytona']).map(receiver => ({ receiver,
  fixmes: receiver === 'ctx.daytona' ? 1 : 0 })))
('requires receiver identity rather than arbitrary injection keys: $receiver', async ({ receiver, fixmes }) => {
  // Given explicit injection keys with independent source-level SDK evidence.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': "import { Daytona } from '@daytonaio/sdk';", 'use.js': `${receiver}.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then a named Daytona property identifies the receiver, but arbitrary string keys do not.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toHaveLength(fixmes);
  expect(contents(root)['use.js']).not.toContain('.v0.create();');
  if (fixmes === 0) expect(contents(root)['use.js']).toBe(`${receiver}.create();`);
  expect(result.code).toBe(fixmes);
});

it.each([
  "import { MyDaytona } from '../outside/my'; new MyDaytona().create();",
  "import { MyDaytona as Client } from '../outside/my'; const client = new Client(); client.create();",
  "import * as sdk from '../outside/my'; new sdk.MyDaytona().create();",
])('reports an outside subclass when its constructor is invoked inside the scan: %s', async source => {
  // Given a subclass definition outside the chosen root, not an outside instance factory.
  const provider = "import { Daytona } from '@daytonaio/sdk'; export class MyDaytona extends Daytona {}";
  const root = temporaryProject({ 'app/package.json': manifest, 'outside/my.ts': provider, 'app/use.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(join(root, 'app'), ['--strict', '--json']);
  // Then the consumer migrates but the unscanned subclass is reported once.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([expect.objectContaining({
    file: 'use.ts',
    message: 'use.ts uses a Daytona client from ../outside/my.ts, outside the scanned folder; migrate that file too.',
  })]);
  expect(readFileSync(join(root, 'app/use.ts'), 'utf8')).toContain('.v0.create();');
  expect(readFileSync(join(root, 'outside/my.ts'), 'utf8')).toBe(provider);
  expect(result.code).toBe(1);
});
