/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { expect, it } from 'vitest';
import { projects, walk } from '../src/discovery.js';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each(['ts', 'js'].flatMap(language => [true, false].map(typed => ({ language, typed }))))(
  'uses unreferenced $language app config aliases (typed=$typed)', async ({ language, typed }) => {
    // Given an Angular layout: a files:[] root with an unreferenced extending app.
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      [`${language}config.json`]: '{"compilerOptions":{"module":"esnext","moduleResolution":"bundler"},"files":[]}',
      [`${language}config.app.json`]: `{"extends":"./${language}config.json","compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}`,
      'src/daytona.ts': "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
      'src/main.ts': "import { client } from '@/daytona'; client.create();" });
    if (typed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then the consumer is migrated or reported, never silently missed.
    const report: Report = JSON.parse(result.output);
    expect(contents(root)['src/main.ts']).toContain(typed ? 'client.v0.create()' : 'FIXME(daytona-upgrade)');
    expect(report.items.filter(item => item.file === 'src/main.ts' && item.kind === 'fixme')).toHaveLength(typed ? 0 : 1);
    expect(result.code).toBe(typed ? 0 : 1);
  },
);

it.each(['@/daytona', '#infra', 'local-infra/client'].map(module => ({ module, fixmes: module === '@/daytona' ? 1 : 0 })))
('requires explicit Daytona identity for an unconfigured module: $module', async ({ module, fixmes }) => {
  // Given an import that no owning-config mapping or installed package resolves.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': `import { client } from '${module}'; client.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toHaveLength(fixmes);
  if (fixmes) expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade)');
  else expect(contents(root)['index.ts']).toBe(`import { client } from '${module}'; client.create();`);
  expect(contents(root)['index.ts']).not.toContain('client.v0.create();');
  expect(result.code).toBe(fixmes);
});

it('selects matching app/spec configs over the same-directory generic config', () => {
  // Given overlapping configs, each with distinct options and include scopes.
  const root = temporaryProject({ 'tsconfig.json': '{}',
    'tsconfig.app.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src/app.ts"]}',
    'tsconfig.spec.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["test/*"]}},"include":["src/*.spec.ts"]}',
    'src/app.ts': 'export {};', 'src/app.spec.ts': 'export {};', 'other.ts': 'export {};' });
  // When
  const sources = projects(root, walk(root)).flatMap(group => group.sources);
  // Then each file is owned by its nearest matching config, without spec aliases leaking.
  expect(sources.find(source => source.getFilePath() === join(root, 'src/app.ts'))?.getProject().getCompilerOptions().paths).toEqual({ '@/*': ['src/*'] });
  expect(sources.find(source => source.getFilePath() === join(root, 'src/app.spec.ts'))?.getProject().getCompilerOptions().paths).toEqual({ '@/*': ['test/*'] });
  expect(sources.find(source => source.getFilePath() === join(root, 'other.ts'))?.getProject().getCompilerOptions().paths).toBeUndefined();
});
