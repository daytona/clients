/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

const fields = [
  'private readonly client: Daytona; constructor(client: Daytona) { this.client = client; }',
  'private client = new Daytona();',
  'private readonly client = new Daytona();',
  'constructor(private readonly client: Daytona) {}',
] as const;

it.each([false, true].flatMap(types => fields.map(field => ({ types, field }))))
('suggests root and service calls when a stable class field holds the client: $field, types=$types', async ({ types, field }) => {
  // Given a property-backed client whose root method runs before its service method.
  const root = temporaryProject({ 'app.ts': `import { Daytona } from '@daytonaio/sdk';
class Worker {
  ${field}
  async run() { await this.client.create(); await this.client.snapshot.get('base'); }
}` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then both calls retain their SDK proof.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.output).toBe(0);
  expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : []))
    .toEqual(expect.arrayContaining(['client.create', 'client.snapshot.get']));
});

it.each([
  'this.client.create = function () { this.snapshot = other; }; this.client.create();',
  'unknown(this.client); this.client.create();',
  'this.client.create(this.client);',
])('suggests the service call as the default pass moves it when a property client escapes: %s', async effect => {
  // Given a mutated or escaped field, not merely a direct SDK method call.
  const root = temporaryProject({ 'app.ts': `import { Daytona } from '@daytonaio/sdk';
class Worker {
  private client = new Daytona();
  async run() { ${effect} await this.client.snapshot.get('base'); }
}` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then the service call gets the suggestion its `.v0` form gets after the
  // default pass, which moves it on the same client proof.
  const report: Report = JSON.parse(result.output);
  expect(report.items.some(item => item.suggestion?.id === 'client.snapshot.get')).toBe(true);
});
