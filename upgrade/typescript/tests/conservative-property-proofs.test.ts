/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const escapes = [
  "function mutate(xs) { xs['0'].client = other; } mutate([holder]);",
  'const args = []; args[0] = holder; unknown(...args);',
  'Number(holder);',
  'String(holder);',
  'function helper(x) { return !x; } helper(holder);',
  'unknown(holder);',
  'unknown(...[holder]);',
  'unknown({nested: [{holder}]});',
  'unknown([[[holder]]]);',
  'unknown((holder as object));',
  'unknown(<object>holder);',
  'unknown(holder!);',
  'unknown(holder satisfies object);',
  'new Unknown({nested: [holder]});',
  'new Unknown(...[holder]);',
  'holder.inspect();',
  'function change() { holder.client = other; } change();',
  'const change = () => { holder.client = other; }; change();',
  '(() => { holder.client = other; })();',
  'unknown(() => holder.client);',
  'new Unknown(() => holder.client);',
  'const alias = holder;',
  'const values = [holder];',
  'const values = {nested: {holder}};',
  'const values = {}; values.saved = holder;',
  'const values = []; values[0] = holder;',
] as const;

it.each([false, true].flatMap(types => escapes.map(escape => ({ types, escape }))))
('reviews all holder property proofs when types=$types and $escape', async ({ types, escape }) => {
  // Given a holder exposed to a call, constructor, called closure, or another storage location.
  const root = temporaryProject({ 'app.ts': prefix +
    `const other = {create() {}}; const holder = {client: new Daytona(), second: new Daytona(), inspect() {}};
${escape}
holder.client.create(); holder.second.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then both linked properties require review without a guessed namespace.
  expect(result.code).toBe(1);
  expect(result.errors).toBe('');
  expect(contents(root)['app.ts']).not.toContain('.v0.create()');
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toHaveLength(2);
});

it.each([
  'unknown(other);',
  'new Unknown({nested: [other]});',
  'unknown(holder.client);',
  'new Unknown(holder.client);',
  'const clientAlias = holder.client; unknown(clientAlias);',
  'const unused = () => holder.client;',
])('keeps holder property proofs when only another value escapes: %s', async escape => {
  // Given no escape of the holder itself, including a client passed directly.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}', 'app.js': prefix +
    `const other = {}; const holder = {client: new Daytona()}; ${escape} holder.client.create();` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.js']).toContain('holder.client.v0.create()');
});

it.each([
  'for (let i = 0; i < 2; i++) { BODY }',
  'for (const x of [0, 1]) { BODY }',
  'for (const x in [0, 1]) { BODY }',
  'while (again) { BODY }',
  'do { BODY } while (again);',
  '[0, 1].forEach(() => { BODY });',
  '[0, 1].map(() => { BODY });',
].flatMap(loop => ['unknown(holder);', "Reflect.set(client, 'create', stub);"].map(effect => ({ loop, effect }))))
('reviews earlier source offsets across a loop back-edge: $loop $effect', async ({ loop, effect }) => {
  // Given an effect after a use in one iteration, but before it on the next.
  const receiver = effect.startsWith('unknown') ? 'holder.client' : 'client';
  const root = temporaryProject({ 'app.js': prefix +
    `const client = new Daytona(); const holder = {client: new Daytona()};
${loop.replace('BODY', `${receiver}.create(); ${effect}`)}` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['app.js']).not.toContain(`${receiver}.v0.create()`);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});
