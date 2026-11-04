/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Ajv2020 } from 'ajv/dist/2020.js';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { UpgradeError } from '../src/model.js';
import { contents, fixture, here, invoke } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

const schemaPath = join(here, '../../mapping/report.schema.json');
function report(output: string): Report {
  const schema: unknown = JSON.parse(readFileSync(schemaPath, 'utf8'));
  if (typeof schema !== 'object' || schema === null) throw new UpgradeError('Invalid report schema');
  const validator = new Ajv2020({ allErrors: true }).compile<Report>(schema);
  const value: unknown = JSON.parse(output);
  if (!validator(value)) throw new UpgradeError(JSON.stringify(validator.errors));
  return value;
}
it.skipIf(!existsSync(schemaPath))('validates upgrade JSON when manual items remain', async () => {
  // Given
  const root = fixture('config-fields');
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const value = report(result.output);
  expect(value.language).toBe('ts');
  expect(value.items.filter(item => item.kind === 'config-field')).toHaveLength(2);
  expect(value.items.filter(item => item.kind === 'deprecated-target')).toHaveLength(1);
  expect(value.items.filter(item => item.kind === 'types-unresolved')).toHaveLength(1);
});
it.skipIf(!existsSync(schemaPath))('validates refusal JSON when custom URLs occur at multiple locations', async () => {
  // Given
  const root = fixture('custom-api-url-stop');
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const value = report(result.output);
  expect(value.refused?.reason).toBe('custom-api-url');
  expect(value.items.filter(item => item.kind === 'custom-api-url').map(item => item.line)).toEqual([2, 3]);
  expect(value.changedFiles).toEqual([]);
  expect(contents(root)).toEqual(before);
});
it.skipIf(!existsSync(schemaPath))('reports suggestions without writes when JSON is requested', async () => {
  // Given
  const root = fixture('suggest-v2-workflow-header');
  const before = contents(root);
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], suggestionMapping);
  // Then
  const value = report(result.output);
  expect(value.mode).toBe('suggest-v2');
  expect(value.dryRun).toBe(true);
  expect(value.dependencies).toEqual([]);
  expect(value.items.filter(item => item.kind === 'suggestion')).toHaveLength(3);
  expect(value.items.filter(item => item.kind === 'workflow')).toHaveLength(1);
  expect(contents(root)).toEqual(before);
});
it.skipIf(!existsSync(schemaPath))('uses resolved types without fallback warnings when a helper returns a client', async () => {
  // Given
  const root = fixture('type-checker');
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const value = report(result.output);
  expect(value.items).toEqual([]);
  expect(contents(root)['index.ts']).toContain('await arbitrary.v0.create()');
});
it.skipIf(!existsSync(schemaPath))('reports peer dependencies without changing them when upgrading code', async () => {
  // Given
  const root = fixture('peer-dependency');
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const value = report(result.output);
  expect(value.items.filter(item => item.kind === 'peer-dependency')).toHaveLength(1);
  expect(value.dependencies).toEqual([]);
});
it.skipIf(!existsSync(schemaPath))('reports the Python command when another ecosystem is present', async () => {
  // Given
  const root = fixture('other-ecosystem');
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(report(result.output).items.filter(item => item.kind === 'other-ecosystem')).toEqual([
    expect.objectContaining({ file: 'pyproject.toml', message: expect.stringContaining('uvx daytona-upgrade') }),
  ]);
});
it('keeps the bundled mapping byte-identical when the source mapping is available', context => {
  // Given
  const canonical = join(here, '../../mapping/mapping.json');
  if (!existsSync(canonical)) { context.skip(); return; }
  // When
  const bundled = readFileSync(join(here, '../src/mapping.json'));
  // Then
  expect(bundled.equals(readFileSync(canonical))).toBe(true);
});
