/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const manifest = '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}';
const mutations = [
  'x.client = other;',
  'Object.assign(x, {client: other});',
  "Object.defineProperty(x, 'client', {value: other});",
  "Reflect.set(x, 'client', other);",
  'delete x.client;',
  'unknown(x);',
] as const;

it.each([false, true].flatMap(types => [false, true].flatMap(imported =>
  mutations.map(mutation => ({ types, imported, mutation })))))
('reviews escaped field proofs when types=$types imported=$imported: $mutation', async ({ types, imported, mutation }) => {
  // Given a helper that can replace the holder's client, including across a module boundary.
  const helper = `export function change(x) { const other = {create() {}}; ${mutation} }`;
  const root = temporaryProject({
    'package.json': manifest,
    'helper.js': helper,
    'app.js': prefix + (imported ? "import { change } from './helper.js';\n" : helper) +
      '\nconst holder = {client: new Daytona()}; change(holder); holder.client.create();',
  });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then an uncertain property never receives a guessed namespace.
  expect(result.code).toBe(1);
  expect(result.errors).toBe('');
  expect(contents(root)['app.js']).not.toContain('holder.client.v0.create()');
  expect(contents(root)['app.js']).toContain('holder.client.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme', file: 'app.js' }));
});

it.each([
  'unknown(holder);',
  'const alias = holder; unknown(alias);',
  'unknown(...[holder]);',
  'const args = [holder]; unknown(...args);',
  'holder.change();',
  'const alias = holder; alias.change();',
])('reviews escaped property proofs when $call', async call => {
  // Given an opaque call or a method that mutates its own holder.
  const root = temporaryProject({ 'package.json': manifest, 'app.js': prefix +
    `const holder = {client: new Daytona(), change() { this.client = other; }}; ${call} holder.client.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['app.js']).not.toContain('holder.client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});

it.each([
  'function helper(x) { return x.client; } helper(holder);',
  'const helper = x => x.client; helper(holder);',
  "console.log(holder); JSON.stringify(holder); String(holder);",
  'function helper(x) {} helper(holder);',
  'function helper(x) { console.log(x); } helper(holder);',
  'holder.inspect();',
])('reviews property proofs even when the call only reads: %s', async call => {
  // Given a holder passed to a call or used as its receiver; no safe-call exemptions.
  const root = temporaryProject({ 'package.json': manifest, 'app.js': prefix +
    `const holder = {client: new Daytona(), inspect() { return this.client; }}; ${call} holder.client.create();` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['app.js']).not.toContain('holder.client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});

it.each([
  'unknown(other);',
  'unknown(holder.client);',
])('preserves property proofs when $call', async call => {
  // Given a harmless call, unrelated argument, or client identity escaping on its own.
  const root = temporaryProject({ 'package.json': manifest, 'app.js': prefix +
    `const holder = {client: new Daytona(), inspect() { return this.client; }}; const other = {}; ${call} holder.client.create();` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.js']).toContain('holder.client.v0.create()');
});

it('preserves client identity when an opaque helper receives the client itself', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.js': prefix + 'const client = new Daytona(); helper(client); client.create();' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.js']).toContain('client.v0.create()');
});

it('reviews property proofs when an imported helper only reads its parameter', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'helper.js': 'export function inspect(x) { return x.client; }',
    'app.js': prefix + "import { inspect } from './helper.js'; const holder = {client: new Daytona()}; inspect(holder); holder.client.create();" });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['app.js']).not.toContain('holder.client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});
