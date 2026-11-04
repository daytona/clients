/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const dependency = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' } as const;
const promises = ["import('@daytonaio/sdk')", "(import('@daytonaio/sdk'))"] as const;
const methods = ['then', 'catch', 'finally'] as const;
const chains = promises.flatMap(promise => methods.flatMap(method => [
  `${promise}.${method}(() => {});`,
  `${promise}['${method}'](() => {});`,
  `await ${promise}.${method}(() => {});`,
]));

it.each(chains)('keeps Promise methods on the dynamic-import review path when using %s', async source => {
  // Given
  const root = temporaryProject({ ...dependency, 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)['index.ts']).toBe(source.replace('@daytonaio/sdk', '@daytona/sdk'));
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'dynamic-import')).toHaveLength(1);
  expect(report.items.filter(item => item.kind === 'private-import')).toEqual([]);
});

it.each([
  "require('@daytonaio/sdk').Sandbox",
  "(require('@daytonaio/sdk'))['Sandbox']",
  "(await import('@daytonaio/sdk')).Sandbox",
  "((await (import('@daytonaio/sdk'))))['Sandbox']",
])('still routes module-value properties to v0 when using %s', async expression => {
  // Given
  const source = `export {};\nconst resource = ${expression};`;
  const root = temporaryProject({ ...dependency, 'index.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['index.ts']).toBe(source.replace('@daytonaio/sdk', '@daytona/sdk/v0'));
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'dynamic-import' || item.kind === 'private-import')).toEqual([]);
});

it.each(methods)('keeps Promise-chain review idempotent when using %s', async method => {
  // Given
  const root = temporaryProject({ ...dependency, 'index.ts': `import('@daytonaio/sdk').${method}(() => {});` });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
  const report: Report = JSON.parse(result.output);
  expect(report.changedFiles).toEqual([]);
  expect(report.items.filter(item => item.kind === 'dynamic-import')).toHaveLength(1);
  expect(report.items.filter(item => item.kind === 'private-import')).toEqual([]);
});
