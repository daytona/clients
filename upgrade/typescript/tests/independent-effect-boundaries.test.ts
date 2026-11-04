/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}';
const prefix = "import { Daytona } from '@daytonaio/sdk';\n";

it.each([
  'function helper(x) { return x.client; } helper(...[holder]);',
  'function helper(_ignored, x) { return x.client; } helper(...[0, holder]);',
  'console.log(...[holder]);',
  'JSON.stringify(...[holder]);',
  'function helper(x) { console.log((x)); } helper(holder);',
])('reviews property proofs whenever a call receives the holder, even read-only: %s', async setup => {
  // The conservative rule drops all holder property proofs; harmless helpers are not exempt.
  const root = temporaryProject({ 'package.json': manifest, 'app.js': prefix +
    `const holder = {client: new Daytona()}; ${setup} holder.client.create();` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['app.js']).not.toContain('holder.client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});

it.each([
  'function helper(x) { const alias = x; } helper(holder);',
  'function helper(x) { unknown((x)); } helper(holder);',
  'function helper(x) { unknown(x as object); } helper(holder);',
  'function helper(x) { return x; } helper(holder);',
  'function helper(x) { x.client++; } helper(holder);',
  'function helper(x) { x[key] = other; } helper(holder);',
  'const console = {log(x) { x.client = other; }}; console.log(holder);',
])('reviews a property when an alias or forwarding can change it: %s', async setup => {
  // Given a helper that does more than observe the parameter.
  const root = temporaryProject({ 'package.json': manifest, 'app.ts': prefix +
    `const holder = {client: new Daytona()}; ${setup} holder.client.create();` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['app.ts']).not.toContain('holder.client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});

it.each(['unknown(holder);', "Reflect.set(client, 'create', stub);"])
('keeps earlier calls proven when the effect occurs afterward: %s', async effect => {
  // Given a same-scope effect that cannot have happened before these calls.
  const root = temporaryProject({ 'package.json': manifest, 'app.js': prefix +
    `const client = new Daytona(); const holder = {client: new Daytona()}; const stub = () => 0;
client.create(); holder.client.create(); ${effect}` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.js']).toContain('client.v0.create()');
  expect(contents(root)['app.js']).toContain('holder.client.v0.create()');
});

it('reviews calls through an alias when a reflective write targets that same instance', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.js': prefix +
    "const client = new Daytona(); const alias = client; Reflect.set(alias, 'create', stub); client.create();" });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['app.js']).not.toContain('client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});
