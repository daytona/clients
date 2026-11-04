/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const dependency = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' } as const;
const helpers = [
  { definition: "function loadSdk() { return require('@daytonaio/sdk') }", call: 'loadSdk()' },
  { definition: "const loadSdk = () => require('@daytonaio/sdk')", call: 'loadSdk()' },
  { definition: "async function loadSdk() { return await import('@daytonaio/sdk') }", call: 'await loadSdk()' },
  { definition: "const loadSdk = async () => import('@daytonaio/sdk')", call: 'await loadSdk()' },
] as const;

it('reports both loaders when migrating the CommonJS and ESM lazy-module example', async () => {
  // Given
  const source = `// CommonJS lazy load
function getDaytona() { return require('@daytonaio/sdk') }
const sdk = getDaytona()
if (err instanceof sdk.DaytonaNotFoundError) {}
new (getDaytona().Daytona)().create()
// ESM dynamic import
async function loadSdk() { return await import('@daytonaio/sdk') }
const m = await loadSdk(); new m.Image()
`;
  const root = temporaryProject({ ...dependency, 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: neither unproven access silently passes strict mode; both loaders require review.
  expect(result.code, result.output).toBe(1);
  expect(contents(root)['index.ts']).toBe(source.replaceAll('@daytonaio/sdk', '@daytona/sdk'));
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'dynamic-import').map(({ file, line }) => ({ file, line })))
    .toEqual([{ file: 'index.ts', line: 2 }, { file: 'index.ts', line: 7 }]);
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toEqual({ dependencies: { '@daytona/sdk': '^2.0.0' } });
});

it.each(helpers.flatMap(helper => [false, true].map(installed => ({ ...helper, installed }))))(
  'requires review for helper-derived exports when $definition, installed=$installed', async ({ definition, call, installed }) => {
    // Given: aliases and inline helper results can mix root clients with v0-only exports.
    const root = temporaryProject({ ...dependency, 'index.ts': `${definition}
const sdk = ${call};
if (err instanceof sdk.DaytonaNotFoundError) {}
new sdk.Image();
new ((${call}).Daytona)().create();
` });
    if (installed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then: review is retained even if installed SDK types prove some downstream receivers.
    expect(result.code, result.output).toBe(1);
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.kind === 'dynamic-import')).toEqual([
      expect.objectContaining({ file: 'index.ts', line: 1 }),
    ]);
    expect(contents(root)['index.ts']).toContain(definition.replace('@daytonaio/sdk', '@daytona/sdk'));
    expect(contents(root)['index.ts']).not.toContain('@daytona/sdk/v0');
  });

it.each(helpers)('retains loader review on rerun when $definition', async ({ definition, call }) => {
  // Given
  const root = temporaryProject({ ...dependency, 'index.ts': `${definition}\nconst sdk = ${call}; new sdk.Image();` });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: renamed specifiers do not make an unresolved resource access pass silently.
  expect(result.code, result.output).toBe(1);
  expect(contents(root)).toEqual(before);
  const report: Report = JSON.parse(result.output);
  expect(report.changedFiles).toEqual([]);
  expect(report.items.filter(item => item.kind === 'dynamic-import')).toHaveLength(1);
});

it('leaves unrelated lazy loaders alone when the project also depends on Daytona', async () => {
  // Given
  const source = "function loadSdk() { return require('unrelated-sdk') }\nnew (loadSdk().Image)();";
  const root = temporaryProject({ ...dependency, 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['index.ts']).toBe(source);
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'dynamic-import' || item.kind === 'fixme')).toEqual([]);
});
