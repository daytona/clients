/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

const sdk = process.env['DAYTONA_UPGRADE_TEST_SDK'];
const modes = ['unresolved', 'getter', 'runtime'] as const;
type Mode = typeof modes[number];
const expected = ['client.create', 'client.snapshot.get', 'client.volume.list'];
const calls = "await client.v0.create(); await client.v0.snapshot.get('base'); await client.v0.volume.list();";
const getterTypes = `interface Service { get(name: string): Promise<string>; list(): Promise<string[]>; }
interface Legacy { create(): Promise<string>; snapshot: Service; volume: Service; }
export declare class Daytona { get v0(): Legacy; }
`;

function installSdk(root: string, mode: Mode): void {
  const destination = join(root, 'node_modules/@daytona/sdk');
  switch (mode) {
    case 'unresolved': return;
    case 'getter':
      mkdirSync(destination, { recursive: true });
      writeFileSync(join(destination, 'package.json'), JSON.stringify({ name: '@daytona/sdk', version: '2.0.0', types: 'index.d.ts' }));
      writeFileSync(join(destination, 'index.d.ts'), getterTypes);
      return;
    case 'runtime':
      if (!sdk) throw new Error('DAYTONA_UPGRADE_TEST_SDK must name a built SDK');
      // Copy the published type surface: symlinks resolve outside node_modules
      // and don't model an installed package's declaration provenance.
      mkdirSync(destination, { recursive: true });
      cpSync(join(resolve(sdk), 'dist'), join(destination, 'dist'), { recursive: true });
      cpSync(join(resolve(sdk), 'package.json'), join(destination, 'package.json'));
      return;
    default: mode satisfies never;
  }
}

function project(source: string): string {
  return temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'tsconfig.json': '{"compilerOptions":{"strict":true,"module":"NodeNext","moduleResolution":"NodeNext"}}',
    'app.ts': source,
  });
}

for (const mode of modes) {
  const test = it.skipIf(mode === 'runtime' && !sdk);
  test(`suggests every namespaced client call when types are ${mode}`, async () => {
    // Given the recommended post-upgrade source, with or without installed types.
    const root = project(`import { Daytona } from '@daytona/sdk'; const client = new Daytona(); ${calls}`);
    installSdk(root, mode);
    // When
    const result = await invoke(root, ['--suggest-v2', '--json']);
    // Then no service suggestion disappears after the first SDK call.
    const report: Report = JSON.parse(result.output);
    expect(result.code, result.errors).toBe(0);
    expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
    expect(report.items.some(item => item.kind === 'types-unresolved')).toBe(mode === 'unresolved');
    expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : [])).toEqual(expected);
  });

  test(`preserves suggestion parity through the default upgrade when types are ${mode}`, async () => {
    // Given the original 0.x source and its independently obtained suggestion set.
    const source = `import { Daytona } from '@daytonaio/sdk'; const client = new Daytona(); ${calls.replaceAll('.v0', '')}`;
    const baseline = project(source);
    const before = await invoke(baseline, ['--suggest-v2', '--json']);
    expect(before.code, before.errors).toBe(0);
    const beforeReport: Report = JSON.parse(before.output);
    const ids = beforeReport.items.flatMap(item => item.suggestion ? [item.suggestion.id] : []);
    expect(ids).toEqual(expected);
    const root = project(source);
    const upgrade = await invoke(root, ['--json']);
    expect(upgrade.code, upgrade.errors).toBe(0);
    expect(readFileSync(join(root, 'app.ts'), 'utf8')).toContain('client.v0.snapshot.get');
    installSdk(root, mode);
    // When the second CLI pass examines the default upgrade's actual output.
    const after = await invoke(root, ['--suggest-v2', '--json']);
    // Then the suggested migrations match the original 0.x program exactly.
    const afterReport: Report = JSON.parse(after.output);
    expect(after.code, after.errors).toBe(0);
    expect(afterReport.items.filter(item => item.kind === 'fixme')).toEqual([]);
    expect(afterReport.items.flatMap(item => item.suggestion ? [item.suggestion.id] : [])).toEqual(ids);
  });

  test(`suggests class-field namespace services when types are ${mode}`, async () => {
    // Given a field-owned client, not a local identifier receiver.
    const root = project(`import { Daytona } from '@daytona/sdk';
class Service {
  private client = new Daytona();
  async run() { ${calls.replaceAll('client.', 'this.client.')} }
}`);
    installSdk(root, mode);
    // When
    const result = await invoke(root, ['--suggest-v2', '--json']);
    // Then services retain the owning field's root-client path.
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
    expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : [])).toEqual(expected);
    expect(report.items.find(item => item.suggestion?.id === 'client.snapshot.get')?.suggestion?.v2)
      .toContain('this.client.v2.');
  });

  test(`does not conflate two clients sharing the namespace getter when types are ${mode}`, async () => {
    // Given an escaped first client and an independent second client.
    const root = project(`import { Daytona } from '@daytona/sdk';
const first = new Daytona(); const second = new Daytona();
unknown(first.v0); await second.v0.snapshot.get('base');`);
    installSdk(root, mode);
    // When
    const result = await invoke(root, ['--suggest-v2', '--json']);
    // Then the first client's escape cannot erase the second client's proof.
    const report: Report = JSON.parse(result.output);
    expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : []))
      .toEqual(['client.snapshot.get']);
  });

  test(`preserves service proofs across SDK-only callbacks when types are ${mode}`, async () => {
    // Given a closure that only reads an SDK method, without exposing the client.
    const root = project(`import { Daytona } from '@daytona/sdk'; const client = new Daytona();
invokeCallback(() => client.v0.create()); await client.v0.snapshot.get('base');`);
    installSdk(root, mode);
    // When
    const result = await invoke(root, ['--suggest-v2', '--json']);
    // Then capturing an SDK-only receiver is not an external client escape.
    const report: Report = JSON.parse(result.output);
    expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : []))
      .toEqual(['client.create', 'client.snapshot.get']);
  });

  test.each([
    'unknown(client.v0);',
    'client.v0.create(client.v0);',
    'client.v0.create = function () { this.snapshot = other; }; client.v0.create();',
  ])(`drops unproven service suggestions with ${mode} types after %s`, async effect => {
    // Given an escaped namespace or a user-replaced method, not a pure SDK call.
    const root = project(`import { Daytona } from '@daytona/sdk'; const client = new Daytona();
${effect} await client.v0.snapshot.get('base');`);
    installSdk(root, mode);
    // When
    const result = await invoke(root, ['--suggest-v2', '--json']);
    // Then the method exemption never grants ownership after a real escape.
    const report: Report = JSON.parse(result.output);
    expect(report.items.some(item => item.suggestion?.id === 'client.snapshot.get')).toBe(false);
  });
}

it.each([
  "const client = unknown(); client.v0.create(); client.v0.snapshot.get('base');",
  "const client = { v0: unknown() }; client.v0.create(); client.v0.snapshot.get('base');",
])('does not infer a client from an unproven v0 receiver: %s', async source => {
  // Given arbitrary values using SDK-like member names.
  const root = project(source);
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then names alone never establish SDK ownership.
  const report: Report = JSON.parse(result.output);
  expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : [])).toEqual([]);
});
