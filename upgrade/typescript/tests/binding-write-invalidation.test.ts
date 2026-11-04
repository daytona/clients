/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project, SyntaxKind } from 'ts-morph';
import { ExportProvenance } from '../src/export-provenance.js';
import { factoryOrigin } from '../src/factory-provenance.js';
import { loadMapping } from '../src/mapping.js';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const writes = [
  '({ client } = other);',
  '({ renamed: client } = other);',
  '({ nested: { client } } = other);',
  '[client] = list;',
  '[, [client]] = list;',
  '({ client = fallback } = other);',
  '[client = fallback] = list;',
  '({...client} = other);',
  '[...client] = list;',
  'for (client of clients) {}',
  'for (client in other) {}',
  'for ({ client } of clients) {}',
  'for ([client] of clients) {}',
] as const;

it.each(writes.flatMap(write => [false, true].map(callable => ({ write, callable }))))(
  'invalidates initializer provenance when callable=$callable and $write', ({ write, callable }) => {
    // Given an initially unrelated binding subsequently written through an opaque target.
    const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
    const source = project.createSourceFile('/provider.js',
      `let client = ${callable ? '() => ({})' : '{}'}; ${write}`);
    const binding = source.getFirstDescendantByKindOrThrow(SyntaxKind.VariableDeclaration);
    const trace = new ExportProvenance(loadMapping(), []);
    // When
    const origin = callable ? factoryOrigin(binding, new Set(), trace) : trace.client(binding);
    // Then the initializer alone cannot prove that all later values are unrelated.
    expect(origin).toBe('unknown');
  });

it.each([
  '({ client: other } = object);',
  '({ [client]: other } = object);',
  'object[client] = other;',
  'function replace() { let client; for (client of clients) {} }',
])('preserves provenance when a write does not target the binding: %s', write => {
  // Given similarly named reads, keys, or a shadowed binding.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/provider.js', `let client = {}; ${write}`);
  const binding = source.getFirstDescendantByKindOrThrow(SyntaxKind.VariableDeclaration);
  // When
  const origin = new ExportProvenance(loadMapping(), []).client(binding);
  // Then unrelated writes do not invalidate the original non-client proof.
  expect(origin).toBe('not-daytona');
});

it.each(['({ client } = other);', '[client] = list;', 'for (client of clients) {}', 'for (client in other) {}'])(
  'leaves generic imported receivers clean after opaque binding changes: %s', async write => {
    // Given a provider with an unrelated SDK import and a non-client initializer.
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'provider.js': `import { Daytona } from '@daytonaio/sdk'; export let client = {}; ${write}`,
      'use.js': "import { client } from './provider.js'; client.create();" });
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then losing non-client proof does not itself establish Daytona evidence.
    const report: Report = JSON.parse(result.output);
    expect(report.items).toEqual([]);
    expect(contents(root)['use.js']).toBe("import { client } from './provider.js'; client.create();");
    expect(result.code).toBe(0);
  });
