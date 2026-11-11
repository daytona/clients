/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';\n";
const unresolved = 'cannot verify the API URL of this DaytonaConfig; if it targets Daytona Cloud, rerun with --drop-api-url';
const asserted = '--drop-api-url asserts Cloud usage; review this unchanged call manually.';

it.each(['', ': DaytonaConfig'].flatMap(type => [
  'function run() { return new Daytona(cfg).create(); }',
  'class App { run() { return new Daytona(cfg).create(); } }',
  'class App { constructor() { new Daytona(cfg).create(); } }',
].map(use => ({ type, use }))))('resolves a module config in nested code: $type $use', async ({ type, use }) => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + `const cfg${type} = { apiKey: 'key' };\n${use}` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('new Daytona(cfg).v0.create()');
});

it.each([
  'console.log(cfg);', 'console.dir(cfg);', 'console.warn(cfg);',
  'JSON.stringify(cfg);', 'JSON.stringify({ cfg });',
  "import * as util from 'node:util'; util.inspect(cfg);",
  "import util from 'util'; util.inspect(cfg);",
  "import { inspect as show } from 'node:util'; show(cfg);",
  'consume(cfg.apiKey);', 'consume(cfg["apiKey"]);',
  'const holder = {}; holder.key = cfg.apiKey; consume(holder.key);',
  'const holder = {}; Object.assign(holder, { apiKey: cfg.apiKey }); consume(holder.apiKey);',
])('permits read-only config use: %s', async read => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + `const cfg = { apiKey: 'key' }; ${read} new Daytona(cfg).create();` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('new Daytona(cfg).v0.create()');
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'config-field')).toEqual([]);
});

it.each([
  'configure(cfg);', 'configure({ cfg });', 'configure([cfg]);',
  'const holder = { cfg }; configure(holder.cfg);',
  'const holder = { cfg }; const alias = holder; configure(alias.cfg);',
  'const holder = { nested: [cfg] }; const alias = holder; configure(alias);',
  'const alias = cfg; configure({ options: alias });',
  'const holder = {}; holder.cfg = cfg; configure(holder.cfg);',
  'const holder = { cfg: {} }; holder.cfg = cfg; configure(holder.cfg);',
  'const holder = {}; holder["cfg"] = cfg; configure(holder.cfg);',
  'const holder = {}; holder.options = { cfg }; configure(holder.options);',
  'const holder = {}; Object.assign(holder, { cfg }); configure(holder.cfg);',
  'const holder = { cfg: {} }; Object.assign(holder, { cfg }); configure(holder.cfg);',
  'function mutate() { configure({ cfg }); }',
  'const console = { log: configure }; console.log(cfg);',
  'const JSON = { stringify: configure }; JSON.stringify(cfg);',
  'const util = { inspect: configure }; util.inspect(cfg);',
  "import { inspect } from './custom.js'; inspect(cfg);",
  "import { inspect } from 'node:util'; function run(inspect) { inspect(cfg); }",
])('refuses an unproven escape without writing: %s', async escape => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + `const cfg = { apiKey: 'key' }; ${escape} new Daytona(cfg).create();`,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({
    refused: { message: 'Cannot verify that every DaytonaConfig targets Daytona Cloud. If they do, rerun with --drop-api-url; for a custom API URL, stay on SDK 0.x. No files were written.' },
    items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url', message: unresolved })]),
    nextSteps: expect.arrayContaining(['rerun with --drop-api-url if every DaytonaConfig targets Daytona Cloud']),
  });
});

it.each(['undefined', 'null', "'https://app.daytona.io/api'", "'https://app.daytona.io/api/'"].flatMap(value =>
  ['apiUrl', 'serverUrl'].map(field => ({ value, field }))))('drops a default or Cloud URL assignment: $field = $value', async ({ value, field }) => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + `const cfg = {}; cfg.${field} = ${value}; new Daytona(cfg).create();` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain(`cfg.${field}`);
  expect(contents(root)['index.ts']).toContain('new Daytona(cfg).v0.create()');
});

it.each([false, true].flatMap(drop => [
  "function mutate() { cfg.apiUrl = 'https://custom.example'; }",
  "const holder = { cfg }; function mutate() { holder.cfg.apiUrl = 'https://custom.example'; }",
  "const alias = cfg; alias.apiUrl = 'https://custom.example';",
].map(write => ({ drop, write }))))('refuses a known custom nested write with drop=$drop: $write', async ({ drop, write }) => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + `const cfg = {}; ${write} new Daytona(cfg);` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output).refused.message).toBe('SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x. No files were written.');
});

it.each([
  'configure({ cfg });',
  'const holder = {}; holder.cfg = cfg; configure(holder.cfg);',
  'const holder = {}; Object.assign(holder, { cfg }); configure(holder.cfg);',
])('reports an asserted aggregate escape for manual review: %s', async escape => {
  // Given
  const root = temporaryProject({ 'index.ts': prefix + `const cfg = {}; ${escape} new Daytona(cfg).create();` });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('new Daytona(cfg).v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'config-field', message: asserted }));
});
