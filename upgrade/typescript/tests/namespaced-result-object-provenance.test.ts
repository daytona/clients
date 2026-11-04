/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const sdk = "import { Daytona } from '@daytonaio/sdk'; const root = new Daytona();";

it.each([
  ['awaited result', 'const object = await root.v0.create();'],
  ['promise result', 'const object = root.v0.create();'],
  ['local alias', 'const sandbox = await root.v0.create(); const object = sandbox;'],
  ['result property', 'const sandbox = await root.v0.create(); const object = sandbox.process;'],
  ['direct chain', 'const object = (await root.v0.create()).process;'],
  ['factory return', 'function make() { return root.v0.create(); } const object = await make();'],
  ['arrow return', 'const make = () => root.v0.create(); const object = await make();'],
  ['destructured result', 'const { process: object } = await root.v0.create();'],
  ['nested destructuring', 'const { nested: { object } } = { nested: { object: await root.v0.create() } };'],
  ['object property alias', 'const box = { object: await root.v0.create() }; const object = box.object;'],
  ['destructured method', 'const { create: make } = root.v0; const object = await make();'],
  ['method alias', 'const make = root.v0.create; const object = await make();'],
  ['conditional results', 'const object = flag ? await root.v0.create() : await root.v0.get("id");'],
  ['computed access', 'const object = await root["v0"]["create"]();'],
])('leaves %s clean when migrated SDK types are missing', async (_name, setup) => {
  // Given an already namespaced value, with no installed SDK declarations.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': `${sdk}\n${setup}\nobject.delete(); object.get(); object.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then sandbox/service operations are neither root-rewritten nor flagged.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['main.js']).toContain('object.delete(); object.get(); object.create();');
});

it.each([
  ['named alias', 'export const sandbox = root.v0.create();', "import { sandbox as object } from './provider.js';"],
  ['default alias', 'export default root.v0.create();', "import object from './provider.js';"],
  ['factory alias', 'export function make() { return root.v0.create(); }', "import { make } from './provider.js'; const object = await make();"],
  ['namespace property', 'export const sandbox = root.v0.create();', "import * as values from './provider.js'; const object = values.sandbox;"],
  ['re-exported alias', 'export const sandbox = root.v0.create();', "import { object } from './barrel.js';"],
  ['factory object property', 'export function make() { return { sandbox: root.v0.create() }; }', "import { make } from './provider.js'; const { sandbox: object } = make();"],
])('leaves imported %s clean without SDK types', async (_name, provider, consumer) => {
  // Given local modules that preserve a v0-produced object across exports.
  const root = temporaryProject({ 'package.json': manifest,
    'provider.js': `${sdk}\n${provider}`,
    'barrel.js': "export { sandbox as object } from './provider.js';",
    'use.js': `${consumer}\nobject.delete(); object.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the consumer has no root rewrite or manual-review item.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'use.js' && item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['use.js']).toContain('object.delete(); object.create();');
});

it.each([
  'let object = await root.v0.create(); object = new Daytona();',
  'const object = flag ? await root.v0.create() : new Daytona();',
  'function make() { if (flag) return root.v0.create(); return new Daytona(); } const object = await make();',
  'function make() { return root.v0.create(); } make = () => new Daytona(); const object = await make();',
  'const box = { object: await root.v0.create() }; box.object = new Daytona(); const object = box.object;',
])('retains review for an SDK root alternative: %s', async setup => {
  // Given a v0 result that does not prove every possible receiver is non-root.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': `${sdk}\n${setup}\nobject.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then uncertainty is not silently suppressed by the v0 alternative.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'main.js', kind: 'fixme' }));
  expect(result.code).toBe(1);
});
