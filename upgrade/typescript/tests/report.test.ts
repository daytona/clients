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
function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function publishedSchema(): Readonly<Record<string, unknown>> {
  const schema: unknown = JSON.parse(readFileSync(schemaPath, 'utf8'));
  if (!isRecord(schema)) throw new UpgradeError('Invalid report schema');
  return schema;
}
// The published schema stays open so reports from later 2.x releases validate;
// the tool's own output is held to this strict copy: no unlisted fields and only
// the values listed under `examples` (mirrored in Python's tests/report_schema.py).
function tightened(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(tightened);
  if (!isRecord(node)) return node;
  const copy: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(node)) copy[key] = key === 'examples' ? value : tightened(value);
  if ('properties' in copy) copy['additionalProperties'] = false;
  if (Array.isArray(copy['examples'])) copy['enum'] = copy['examples'];
  return copy;
}
function strictSchema(): Readonly<Record<string, unknown>> {
  const schema = tightened(publishedSchema());
  if (!isRecord(schema)) throw new UpgradeError('Invalid report schema');
  return schema;
}
function report(output: string): Report {
  const validator = new Ajv2020({ allErrors: true }).compile<Report>(strictSchema());
  const value: unknown = JSON.parse(output);
  if (!validator(value)) throw new UpgradeError(JSON.stringify(validator.errors));
  return value;
}
const laterReport = {
  tool: 'daytona-upgrade', version: '2.1.0', language: 'later-language', mode: 'later-mode', root: '/project', dryRun: false,
  changedFiles: [], nextSteps: [], laterField: true,
  dependencies: [{ file: 'package.json', from: '^0.230.0', to: '^2.1.0', laterDependencyField: 'x' }],
  items: [{
    file: 'app.ts', line: 1, column: 1, kind: 'later-kind', message: 'Review this call.', laterItemField: 'x',
    suggestion: {
      id: 'sandbox.process.executeCommand', receiver: 'sandbox.process', v0: 'sandbox.process.executeCommand',
      v2: 'sandbox.process.run', category: 'later-category', notes: [], docs: 'https://www.daytona.io/docs', laterSuggestionField: 'x',
    },
  }],
  refused: { reason: 'later-reason', message: 'Refused.', laterRefusedField: 'x' },
  warnings: [{ file: 'app.ts', message: 'Review this.', laterWarningField: 'x' }],
};
function schemaNodes(node: unknown): Readonly<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap(schemaNodes);
  if (!isRecord(node)) return [];
  return [node, ...Object.entries(node).filter(([key]) => key !== 'examples').flatMap(([, value]) => schemaNodes(value))];
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
it.skipIf(!existsSync(schemaPath))('accepts fields and values a later 2.x release adds', () => {
  // Given
  const validator = new Ajv2020({ allErrors: true }).compile(publishedSchema());
  // When
  const valid = validator(laterReport);
  // Then
  expect(validator.errors ?? []).toEqual([]);
  expect(valid).toBe(true);
});
it.skipIf(!existsSync(schemaPath))('publishes no closed object or vocabulary', () => {
  // Given
  const nodes = schemaNodes(publishedSchema());
  // When
  const closed = nodes.filter(node => node['additionalProperties'] === false || node['unevaluatedProperties'] === false);
  const enums = nodes.filter(node => 'enum' in node);
  const untyped = nodes.filter(node => 'examples' in node && node['type'] !== 'string');
  // Then
  expect(closed).toEqual([]);
  expect(enums).toEqual([]);
  expect(untyped).toEqual([]);
});
it.skipIf(!existsSync(schemaPath))('holds the tool output to the listed fields and values', () => {
  // Given
  const validator = new Ajv2020({ allErrors: true }).compile(strictSchema());
  // When
  const valid = validator(laterReport);
  // Then
  expect(valid).toBe(false);
  expect((validator.errors ?? []).map(error => `${error.instancePath} ${error.keyword}`).sort()).toEqual([
    ' additionalProperties',
    '/dependencies/0 additionalProperties',
    '/items/0 additionalProperties',
    '/items/0/kind enum',
    '/items/0/suggestion additionalProperties',
    '/items/0/suggestion/category enum',
    '/language enum',
    '/mode enum',
    '/refused additionalProperties',
    '/refused/reason enum',
    '/warnings/0 additionalProperties',
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
