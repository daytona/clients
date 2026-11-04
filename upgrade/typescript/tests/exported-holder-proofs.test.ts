/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';
import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const manifest = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' };
const exports = [
  { name: 'exported binding', setup: 'export const holder = { client: new Daytona() };', exported: 'holder' },
  { name: 'named export', setup: 'const holder = { client: new Daytona() }; export { holder };', exported: 'holder' },
  { name: 'renamed export', setup: 'const holder = { client: new Daytona() }; export { holder as publicHolder };', exported: 'publicHolder' },
  { name: 'default export', setup: 'const holder = { client: new Daytona() }; export default holder;', exported: 'default' },
  { name: 'module.exports property', setup: 'const holder = { client: new Daytona() }; module.exports.holder = holder;', exported: 'holder' },
  { name: 'exports property', setup: 'const holder = { client: new Daytona() }; exports.holder = holder;', exported: 'holder' },
] as const;

it.each([false, true].flatMap(types => exports.map(scenario => ({ types, ...scenario }))))
('reviews escaped properties when $name with types=$types', async ({ types, setup, exported }) => {
  // Given a holder that a consumer can replace after module initialization.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + setup + '\nexport function run() { return holder.client.create(); }' });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the linked, unproven property is reviewed and still calls the consumer's replacement.
  expect(result.code, result.output).toBe(1);
  const text = contents(root)['app.ts'] ?? '';
  expect(text).not.toContain('holder.client.v0.create()');
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  const javascript = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const bindings = {};
  class Daytona { create() { return 'sdk'; } }
  const replace = `exports[${JSON.stringify(exported)}].client = { create: () => 'local' }; exports.run();`;
  expect(runInNewContext(javascript + replace,
    { require: () => ({ Daytona }), exports: bindings, module: { exports: bindings } })).toBe('local');
});

it.each([false, true].flatMap(types => ['readonly client: Daytona', 'readonly client', 'client: Daytona'].map(field => ({ types, field }))))
('retains only proof-grade exported fields when $field with types=$types', async ({ types, field }) => {
  // Given a readonly or annotated client field exposed through an exported instance.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Holder { ${field} = new Daytona(); }
export const holder = new Holder();
holder.client.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then unresolved mutable annotations are only linked evidence; resolved SDK types survive.
  const proven = field.startsWith('readonly') || types;
  expect(result.code, result.output).toBe(proven && types ? 0 : 1);
  expect(contents(root)['app.ts']?.includes('holder.client.v0.create()')).toBe(proven);
});

it.each([false, true].flatMap(types => ['client: Daytona', 'readonly client: Daytona'].map(field => ({ types, field }))))
('retains proof-grade property signatures when $field with types=$types', async ({ types, field }) => {
  // Given a holder with an explicit client property type rather than a class field.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `export const holder: { ${field} } = { client: new Daytona() };
holder.client.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the same readonly/resolved-type rule applies to object holder annotations.
  const proven = field.startsWith('readonly') || types;
  expect(result.code, result.output).toBe(proven && types ? 0 : 1);
  expect(contents(root)['app.ts']?.includes('holder.client.v0.create()')).toBe(proven);
});
