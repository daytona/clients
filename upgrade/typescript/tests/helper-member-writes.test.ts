/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const writes = ["c.create = () => 'local';", "Reflect.set(c, 'create', () => 'local');"] as const;
const scenarios = writes.flatMap(write => [
  { name: 'helper parameter', body: `function stub(c) { ${write} } stub(client); client.create();` },
  { name: 'nested helper', body: `function stub(c) { ${write} } function install(c) { stub(c); } install(client); client.create();` },
  { name: 'aliased arrow helper', body: `const stub = c => { const alias = c; ${write.replace(/\bc\b/gu, 'alias')} }; const install = stub; install(client); client.create();` },
  { name: 'object method helper', body: `const patches = { stub(c) { ${write} } }; patches.stub(client); client.create();` },
  { name: 'loop back-edge', body: `const c = client; let result; for (let i = 0; i < 2; i++) { result = client.create(); ${write} } result;` },
  { name: 'callback back-edge', body: `const c = client; let result; [0, 1].forEach(() => { result = client.create(); ${write} }); result;` },
].map(scenario => ({ ...scenario, write })));

it.each([false, true].flatMap(types => scenarios.map(scenario => ({ types, ...scenario }))))
('reviews calls after $name when types=$types: $write', async ({ types, body }) => {
  // Given a helper or previous iteration replacing a moved method.
  const root = temporaryProject({ 'app.js': prefix + `const client = new Daytona(); ${body}` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the call requires review and still invokes the installed replacement.
  expect(result.code).toBe(1);
  expect(result.errors).toBe('');
  const text = contents(root)['app.js'] ?? '';
  expect(text).not.toContain('client.v0.create()');
  const items: readonly { readonly kind: string }[] = JSON.parse(result.output).items;
  expect(items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  const javascript = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  class Daytona {
    readonly v0 = { create: () => 'wrong-namespace' };
    create() { return 'sdk'; }
  }
  expect(runInNewContext(javascript, { require: () => ({ Daytona }), exports: {} })).toBe('local');
});

it.each([
  { name: 'another instance', setup: "function stub(c) { c.create = () => 'local'; } stub(other);" },
  { name: 'another member', setup: "function stub(c) { Reflect.set(c, 'unrelated', () => 'local'); } stub(client);" },
  { name: 'uncalled helper', setup: "function stub(c) { c.create = () => 'local'; }" },
])('rewrites unaffected calls when a write targets $name', async ({ setup }) => {
  // Given a replacement that cannot affect this instance's create method.
  const root = temporaryProject({ 'app.js': prefix +
    `const client = new Daytona(); const other = new Daytona(); ${setup} client.create();` });
  // When
  const result = await invoke(root, ['--json']);
  // Then the unaffected call retains its automatic upgrade.
  expect(result.code).toBe(0);
  expect(contents(root)['app.js']).toContain('client.v0.create()');
});

it('rewrites an earlier call when a helper installs the replacement later', async () => {
  // Given a same-scope call preceding the helper invocation.
  const root = temporaryProject({ 'app.js': prefix +
    "const client = new Daytona();\nfunction stub(c) { c.create = () => 'local'; }\nclient.create();\nstub(client);\nclient.create();" });
  // When
  const result = await invoke(root, ['--json']);
  // Then only the later call loses its SDK-member proof.
  const text = contents(root)['app.js'] ?? '';
  expect(text.match(/client\.v0\.create\(\)/gu)).toHaveLength(1);
  const items: readonly { readonly kind: string; readonly line: number }[] = JSON.parse(result.output).items;
  expect(items).toContainEqual(expect.objectContaining({ kind: 'fixme', line: 6 }));
});
