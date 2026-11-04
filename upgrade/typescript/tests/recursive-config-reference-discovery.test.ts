/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { chmodSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { projects, walk } from '../src/discovery.js';
import type { Report } from '../src/model.js';
import { ReadFailures } from '../src/read-failures.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const permissionTest = it.skipIf(process.platform === 'win32' || process.getuid?.() === 0);

const invalidReferenceProject = (reference: string): string => {
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.json': `{"references":[{"path":"./${reference}"},{"path":"./tsconfig.app.json"}]}`,
    'tsconfig.app.json': '{"include":["app.ts"]}',
    'app.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n",
    ...(reference === 'missing/tsconfig.json' ? {} : { [reference]: '{}' }),
  });
  installFakeSdk(root);
  return root;
};

it('reports a missing referenced config and continues discovering valid configs', async () => {
  // Given a solution with one missing reference and one valid app config.
  const root = invalidReferenceProject('missing/tsconfig.json');
  const before = contents(root);
  expect(projects(root, walk(root), new ReadFailures(root)).flatMap(group => group.sources.map(source => source.getBaseName()))).toContain('app.ts');

  // When discovery runs.
  const result = await invoke(root, ['--strict', '--json']);

  // Then it reports the unreadable path, finds the valid app, and writes nothing.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'missing/tsconfig.json', kind: 'unsupported-file',
    message: expect.stringContaining('missing/tsconfig.json could not be read:') }));
  expect(report.changedFiles).toEqual([]);
  expect(contents(root)).toEqual(before);
});

permissionTest('reports an unreadable referenced config and continues discovering valid configs', async () => {
  // Given a solution whose referenced config cannot be read.
  const root = invalidReferenceProject('blocked/tsconfig.json');
  const before = contents(root);
  const config = join(root, 'blocked/tsconfig.json');
  const mode = statSync(config).mode;
  expect(projects(root, walk(root), new ReadFailures(root)).flatMap(group => group.sources.map(source => source.getBaseName()))).toContain('app.ts');
  chmodSync(config, 0);
  try {
    // When discovery runs.
    const result = await invoke(root, ['--strict', '--json']);

    // Then it reports the permission failure, finds the valid app, and writes nothing.
    const report: Report = JSON.parse(result.output);
    expect(result.code).toBe(1);
    expect(report.items).toContainEqual(expect.objectContaining({ file: 'blocked/tsconfig.json', kind: 'unsupported-file',
      message: 'blocked/tsconfig.json could not be read: Permission denied; fix its permissions, then rerun.' }));
    expect(report.changedFiles).toEqual([]);
  } finally { chmodSync(config, mode); }
  expect(contents(root)).toEqual(before);
});

it.each(['', 'web', 'apps/web'].flatMap(directory => [
  { directory, source: "client[method]();", after: 'client[method]()' },
  { directory, source: 'const { create } = client;', after: 'const { create } = client' },
  { directory, source: 'client.create();', after: 'client.v0.create()' },
]))('uses referenced app aliases for $source under "$directory"', async ({ directory, source, after }) => {
  // Given a Vite-style solution with recursive references and overlapping root includes.
  const path = (file: string) => join(directory, file);
  const root = temporaryProject({
    'tsconfig.json': '{}',
    [path('package.json')]: '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    [path('tsconfig.json')]: '{"references":[{"path":"./config/tsconfig.solution.json"},{"path":"./tsconfig.app.json"}]}',
    [path('config/tsconfig.solution.json')]: '{"files":[],"references":[{"path":"../tsconfig.app.json"}]}',
    [path('tsconfig.app.json')]: '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}',
    [path('src/infra.ts')]: "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
    [path('src/app.ts')]: `import { client } from '@/infra'; ${source}`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then computed access and extraction require review; a direct call migrates.
  const report: Report = JSON.parse(result.output);
  const manual = source !== 'client.create();';
  expect(report.items.filter(item => item.kind === 'fixme')).toHaveLength(manual ? 1 : 0);
  if (manual) {
    // Only a FIXME comment line may be added; the code itself is left exactly as written.
    const lines = (contents(root)[path('src/app.ts')] ?? '').split('\n').filter(line => !line.trimStart().startsWith('// FIXME(daytona-upgrade)'));
    expect(lines.join(' ')).toBe(`import { client } from '@/infra'; ${source}`);
  } else {
    expect(contents(root)[path('src/app.ts')]).toContain(after);
  }
  expect(result.code).toBe(manual ? 1 : 0);
});

it('deduplicates recursive config references while preserving nearest ownership', () => {
  // Given a cycle, duplicate edges, directory references, and overlapping includes.
  const root = temporaryProject({
    'tsconfig.json': '{"references":[{"path":"./tsconfig.app.json"},{"path":"./nested"}]}',
    'tsconfig.app.json': '{"compilerOptions":{"strictNullChecks":true},"references":[{"path":"./tsconfig.json"},{"path":"./nested"}]}',
    'nested/tsconfig.json': '{"compilerOptions":{"strictNullChecks":false}}',
    'app.ts': 'export const value = 1;',
    'nested/app.ts': 'export const value = 2;',
  });
  // When
  const groups = projects(root, walk(root));
  // Then every config is loaded once, and each file uses its nearest owning config.
  expect(groups).toHaveLength(3);
  const sources = groups.flatMap(group => group.sources);
  expect(sources).toHaveLength(2);
  expect(sources.find(source => source.getFilePath() === join(root, 'app.ts'))?.getProject().getCompilerOptions().strictNullChecks).toBe(true);
  expect(sources.find(source => source.getFilePath() === join(root, 'nested/app.ts'))?.getProject().getCompilerOptions().strictNullChecks).toBe(false);
});
