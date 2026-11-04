/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\nconst client = new Daytona();\n";
const manifest = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' };
const scenarios = [
  { name: 'constructor parameter', setup: "class Patch { constructor(c) { c.create = () => 'local'; } } new Patch(client);" },
  { name: 'constructor parameter property', setup: "class Patch { constructor(public c: Daytona) { this.c.create = () => 'local'; } } new Patch(client);" },
  { name: 'aliased class expression', setup: "const Patch = class { constructor(c) { Reflect.set(c, 'create', () => 'local'); } }; const Install = Patch; new Install(client);" },
  { name: 'constructor helper', setup: "function patch(c) { c.create = () => 'local'; } class Patch { constructor(c) { patch(c); } } new Patch(client);" },
  { name: 'omitted default', setup: "function patch(c = client) { c.create = () => 'local'; } patch();" },
  { name: 'explicit undefined default', setup: "function patch(c = client) { c.create = () => 'local'; } patch(undefined);" },
  { name: 'constructor default', setup: "class Patch { constructor(c = client) { c.create = () => 'local'; } } new Patch();" },
  { name: 'earlier parameter default', setup: "function patch(first, c = first) { c.create = () => 'local'; } patch(client);" },
  { name: 'literal spread', setup: "function patch(c) { c.create = () => 'local'; } patch(...[client]);" },
  { name: 'aliased spread', setup: "function patch(c) { c.create = () => 'local'; } const args = [client]; patch(...args);" },
  { name: 'spread before argument', setup: "function patch(_, c) { c.create = () => 'local'; } const args = [{}, client]; patch(...args, {});" },
  { name: 'constructor spread', setup: "class Patch { constructor(c) { c.create = () => 'local'; } } const args = [client]; new Patch(...args);" },
] as const;

it.each([false, true].flatMap(types => scenarios.map(scenario => ({ types, ...scenario }))))
('reviews replaced methods when $name with types=$types', async ({ types, setup }) => {
  // Given a project invocation that replaces a client's moved member.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + setup + '\nclient.create();' });
  if (types) installFakeSdk(root);
  // When the CLI migrates the actual project.
  const result = await invoke(root, ['--strict', '--json']);
  // Then the replacement remains callable, rather than being redirected to v0.
  expect(result.code, result.output).toBe(1);
  const text = contents(root)['app.ts'] ?? '';
  expect(text).not.toContain('client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  const javascript = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  class Daytona {
    readonly v0 = { create: () => 'wrong-namespace' };
    create() { return 'sdk'; }
  }
  expect(runInNewContext(javascript, { require: () => ({ Daytona }), exports: {} })).toBe('local');
});

it.each([
  { name: 'uncalled constructor', setup: "class Patch { constructor(c) { c.create = () => 'local'; } }" },
  { name: 'another constructor argument', setup: "class Patch { constructor(c) { c.create = () => 'local'; } } new Patch(new Daytona());" },
  { name: 'overridden default', setup: "function patch(c = client) { c.create = () => 'local'; } patch(new Daytona());" },
  { name: 'uncalled default', setup: "function patch(c = client) { c.create = () => 'local'; }" },
  { name: 'spread after written parameter', setup: "function patch(c, ...rest) { c.create = () => 'local'; } patch(new Daytona(), ...[client]);" },
])('rewrites unaffected methods when $name', async ({ setup }) => {
  // Given an invocation whose write cannot reach this client.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + setup + '\nclient.create();' });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toContain('client.v0.create()');
});
