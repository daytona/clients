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
  'await root.v0.create().then(() => root)',
  '(await (root.v0.create())).then(() => root)',
  'await root.v0.create().process.getClient()',
  'await root["v0"]["create"]()["then"](() => root)',
])('reviews a receiver when a later call determines the v0 chain result: %s', async expression => {
  // Given a namespaced call whose later operation may return a root client.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': `${sdk}\nconst client = ${expression};\nclient.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then uncertainty is reported, not silently classified as a v0 object.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'main.js', kind: 'fixme' }));
  expect(contents(root)['main.js']).not.toContain('client.v0.create();');
  expect(result.code).toBe(1);
});

it.each([
  'for (make of [() => root]) {}',
  'for (make in replacements) {}',
  '({ make } = replacements);',
  '[make] = replacements;',
])('reviews factory results when the function binding is replaced: %s', async write => {
  // Given a function declaration that originally returns a v0 object.
  const root = temporaryProject({ 'package.json': manifest,
    'main.js': `${sdk}\nfunction make() { return root.v0.create(); }\n${write}\nconst client = await make();\nclient.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the stale factory return cannot suppress manual review.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'main.js', kind: 'fixme' }));
  expect(contents(root)['main.js']).not.toContain('client.v0.create();');
  expect(result.code).toBe(1);
});
