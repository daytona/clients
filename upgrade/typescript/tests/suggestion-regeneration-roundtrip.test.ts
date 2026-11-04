/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project, ts } from 'ts-morph';
import { suggest } from '../src/suggestions.js';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

it.each([
  "const d = new Daytona(); d.v0.create();",
  "const d = new Daytona();\td.v0.create(); d.v0.list();",
  "const d = new Daytona();d.v0.create();",
  "const d = new Daytona(); function run() { d.v0.create(); }",
  "const d = new Daytona();\r\nfunction run() {\t d.v0.create(); }\r\n",
  "const d = new Daytona();\nfunction run() {\n  if (true) d.v0.create();\n}\n",
])('removes inline suggestions without changing source tokens: %s', async body => {
  // Given
  const input = "import { Daytona } from '@daytona/sdk';\n" + body;
  const root = temporaryProject({ 'app.ts': input });
  const setup = await invoke(root, ['--suggest-v2'], suggestionMapping);
  expect(setup.code).toBe(0);
  expect(contents(root)['app.ts']).toContain('daytona-v2:');
  // When
  const result = await invoke(root, ['--remove-suggestions']);
  // Then
  expect(result.code).toBe(0);
  const after = contents(root)['app.ts'] ?? '';
  expect(after).not.toContain('// daytona-v2:');
  expect(after.replace(/\s/gu, '')).toBe(input.replace(/\s/gu, ''));
  expect(ts.transpileModule(after, { reportDiagnostics: true }).diagnostics ?? []).toEqual([]);
});

it('keeps inline suggestion output identical when suggestions are regenerated', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.v0.create(); d.v0.list();" });
  const setup = await invoke(root, ['--suggest-v2'], suggestionMapping);
  expect(setup.code).toBe(0);
  const before = contents(root);
  expect(before['app.ts']).toContain('daytona-v2:');
  // When
  const result = await invoke(root, ['--suggest-v2'], suggestionMapping);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)).toEqual(before);
});

it('keeps shared-client suggestions stable when several files are regenerated', async () => {
  // Given
  const root = temporaryProject({
    'a.ts': "import { d } from './z'; d.v0.list();",
    'z.ts': "import { Daytona } from '@daytona/sdk'; export const d = new Daytona(); d.v0.create();",
  });
  const setup = await invoke(root, ['--suggest-v2'], suggestionMapping);
  expect(setup.code).toBe(0);
  const before = contents(root);
  expect(before['a.ts']).toContain('daytona-v2:');
  expect(before['z.ts']).toContain('daytona-v2:');
  // When
  const result = await invoke(root, ['--suggest-v2'], suggestionMapping);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)).toEqual(before);
});

it.each([
  { body: 'const d = new Daytona();\r\nd.create();\r\nd.list();\r\n', initial: 4 },
  { body: 'const d = new Daytona(); d.create(); d.list();\r\n', initial: 2 },
])('preserves CRLF across upgrade, repeated suggestions and removal with $initial original breaks', async ({ body, initial }) => {
  // Given
  const input = "import { Daytona } from '@daytonaio/sdk';\r\n" + body;
  const root = temporaryProject({ 'app.ts': input });
  // When
  const upgrade = await invoke(root);
  const upgraded = contents(root)['app.ts'] ?? '';
  const first = await invoke(root, ['--suggest-v2'], suggestionMapping);
  const suggested = contents(root)['app.ts'] ?? '';
  const second = await invoke(root, ['--suggest-v2'], suggestionMapping);
  const repeated = contents(root)['app.ts'] ?? '';
  const remove = await invoke(root, ['--remove-suggestions']);
  const removed = contents(root)['app.ts'] ?? '';
  // Then
  expect([upgrade.code, first.code, second.code, remove.code]).toEqual([0, 0, 0, 0]);
  expect([upgraded, suggested, repeated, removed].map(text => ({
    cr: (text.match(/\r/gu) ?? []).length,
    lf: (text.match(/\n/gu) ?? []).length,
  }))).toEqual([{ cr: initial, lf: initial }, { cr: 9, lf: 9 }, { cr: 9, lf: 9 }, { cr: 4, lf: 4 }]);
  expect(repeated).toBe(suggested);
  expect(removed.replace(/\s/gu, '')).toBe(upgraded.replace(/\s/gu, ''));
});

it('deletes a reserved comment without joining neighboring statements', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': 'const first = 1\n// daytona-v2: example note\nconst second = 2\n' });
  // When
  const result = await invoke(root, ['--remove-suggestions']);
  // Then
  const after = contents(root)['app.ts'] ?? '';
  expect(result.code).toBe(0);
  expect(after).toBe('const first = 1\nconst second = 2\n');
  expect(ts.transpileModule(after, { reportDiagnostics: true }).diagnostics ?? []).toEqual([]);
});

it('removes every own-line reserved comment even when it was hand-written', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': '// daytona-v2: hand-written\r\n// ordinary comment\r\n  // daytona-v2: another note\r\nconst value = 1;\r\n' });
  // When
  const result = await invoke(root, ['--remove-suggestions']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toBe('// ordinary comment\r\nconst value = 1;\r\n');
});

it('keeps the analysis AST unchanged when replacing existing suggestion comments', () => {
  // Given
  const input = "import { Daytona } from '@daytona/sdk';\r\nconst d = new Daytona();\r\n// daytona-v2: stale\r\nd.v0.list();\r\n";
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/app.ts', input);
  // When
  const result = suggest(source, suggestionMapping, '/');
  // Then
  expect(source.getFullText()).toBe(input);
  expect(result.after).not.toContain('// daytona-v2: stale');
  expect(result.items.filter(item => item.kind === 'suggestion')).toHaveLength(1);
  expect((result.after.match(/\r/gu) ?? []).length).toBe(5);
  expect(result.after.replace(/\r\n/gu, '')).not.toContain('\n');
});
