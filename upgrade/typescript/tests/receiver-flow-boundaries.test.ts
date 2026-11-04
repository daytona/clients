/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { supplied: "() => ({ Daytona: class { delete() { return 'local'; } } })", count: 0 },
  { supplied: "() => ({ Daytona: class {}, unrelated: SDK })", count: 0 },
  { supplied: '() => ({ Daytona: SDK })', count: 1 },
  { supplied: '() => ({ Daytona: class extends SDK {} })', count: 1 },
  { supplied: 'opaque', count: 1 },
])('preserves constructor provenance through a local loader when supplied $supplied', async ({ supplied, count }) => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.js': `import { Daytona as SDK } from '@daytonaio/sdk';
function use(require) { const { Daytona } = require('local'); return new Daytona().delete(); }
use(${supplied});
`,
  });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toHaveLength(count);
  expect(contents(root)['index.js']).toContain('new Daytona().delete()');
});

it.each([
  { setup: '', payload: 'new Daytona()', count: 1 },
  { setup: 'declare const opaque: unknown;', payload: 'opaque', count: 0 },
  { setup: 'const Promise = { resolve(value: unknown) { return {}; } };', payload: 'new Daytona()', count: 0 },
])('keeps only linked native Promise payloads reviewable when $setup / $payload', async ({ setup, payload, count }) => {
  // Given
  const expression = `(await Promise.resolve(${payload})).create()`;
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': `import { Daytona } from '@daytonaio/sdk';\n${setup}\nasync function run() { ${expression}; }`,
  });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'fixme')).toHaveLength(count);
  expect(contents(root)['index.ts']).toContain(expression);
});
