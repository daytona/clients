/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';
import { ts } from 'ts-morph';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const replacements = [
  "Reflect.set(service, 'client', local);",
  "Object.defineProperty(service, 'client', {value: local});",
  "Object.defineProperties(service, {client: {value: local}});",
  "const alias = service; Reflect.set(alias, 'client', local);",
  "const alias = service; Object.defineProperty(alias, 'client', {value: local});",
] as const;
const programs = [
  "function run(client = new Daytona()) { return client.create(); } run({create(){return 'local-cache'}});",
  "class LocalClient extends Daytona { constructor() { return {create(){return 'local-cache'}}; } } const client = new LocalClient(); client.create();",
  "const LocalClient = class extends Daytona { constructor() { return {create(){return 'local-cache'}}; } }; const client = new LocalClient(); client.create();",
  "class Base extends Daytona { constructor() { return {create(){return 'local-cache'}}; } } class Client extends Base {} const client = new Client(); client.create();",
  ...replacements.map(write => "const local = {create(){return 'local-cache'}}; const service = {client: new Daytona()}; " + write + ' service.client.create();'),
] as const;

it.each(programs)('keeps an unproven JavaScript receiver executable with manual review: %s', async program => {
  // Given: no node_modules; a real runtime evaluates the migrated local-object path.
  const root = temporaryProject({ 'app.js': prefix + program });
  class Daytona {}
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const after = contents(root)['app.js'] ?? '';
  expect(result.code).toBe(0);
  expect(after.split('\n').filter(line => !line.trimStart().startsWith('//')).join('\n')).not.toContain('.v0.');
  if (program.startsWith('function run(')) {
    // The explicit local argument overrides the unused Daytona default in this invocation.
    expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
  } else expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]));
  const js = ts.transpileModule(after, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  expect(runInNewContext(js, { require: () => ({ Daytona }), exports: {} })).toBe('local-cache');
});

it.each([false, true])('keeps annotated default parameters proven when installed types=%s', async types => {
  // Given
  const root = temporaryProject({ 'app.ts': prefix + 'function run(client: Daytona = new Daytona()) { return client.create(); }' });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain('return client.v0.create()');
});

it.each([false, true])('keeps ordinary subclasses proven when installed types=%s', async types => {
  // Given: nested function returns do not replace the constructed instance.
  const root = temporaryProject({ 'app.ts': prefix +
    'class Client extends Daytona { constructor() { super(); const helper = () => { return {}; }; } } new Client().create();' });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain('new Client().v0.create()');
});
