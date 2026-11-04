/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { expect, it } from 'vitest';
import { projects, walk } from '../src/discovery.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  { file: 'src/main.spec.ts', app: { include: ['src'] }, spec: { include: ['src/**/*.spec.ts'] }, expected: 'spec' },
  { file: 'src/main.test.ts', app: { include: ['src'] }, spec: { include: ['src'] }, expected: 'spec' },
  { file: 'src/main.ts', app: { files: ['src/main.ts'] }, spec: { include: ['src'] }, expected: 'app' },
  { file: 'src/main.ts', app: { include: ['src'] }, spec: { include: ['src'] }, expected: 'app' },
  { file: 'src/main.spec.ts', app: { include: ['src/main.spec.ts'] }, spec: { include: ['src/*.spec.ts'] }, expected: 'app' },
])('selects $expected ownership for $file with app=$app and spec=$spec', ({ file, app, spec, expected }) => {
  // Given two overlapping configs with distinguishable options.
  const root = temporaryProject({
    'tsconfig.app.json': JSON.stringify({ ...app, compilerOptions: { strict: false } }),
    'tsconfig.spec.json': JSON.stringify({ ...spec, compilerOptions: { strict: true } }), [file]: 'export {};' });
  // When
  const source = projects(root, walk(root)).flatMap(group => group.sources).find(source => source.getFilePath() === join(root, file));
  // Then
  expect(source?.getProject().getCompilerOptions().strict).toBe(expected === 'spec');
});

it.each(['spec', 'e2e', 'eslint'].flatMap(config => [true, false].map(explicit => ({ config, explicit }))))(
  'reports conflicting app/$config imports without rewriting (explicit=$explicit)', async ({ config, explicit }) => {
    // Given two conflicting config layouts: mock test and real files-based app.
    const file = explicit ? 'src/main.ts' : 'src/main.spec.ts';
    const source = "import { client } from '@/d'; client.create();";
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'tsconfig.app.json': JSON.stringify({ compilerOptions: { baseUrl: '.', paths: { '@/*': ['src/*'] } },
        ...(explicit ? { files: [file] } : { include: ['src'] }) }),
      [`tsconfig.${config}.json`]: JSON.stringify({ compilerOptions: { baseUrl: '.', paths: { '@/*': ['stubs/*'] } }, include: ['src'] }),
      'src/d.ts': "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();",
      'stubs/d.ts': 'export const client = { create() {} };', [file]: source });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then no config choice can cause a wrong rewrite or a silent miss.
    expect(contents(root)[file]).toBe(source);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file, kind: 'fixme',
      message: `${file} is included by several tsconfig files that resolve its imports differently; review it manually.` }));
    expect(result.code).toBe(1);
  },
);

it.each([true, false])('keeps agreeing configs safe for client and npm exports (daytona=%s)', async daytona => {
  // Given overlapping configs resolving the same provider.
  const source = "import { client } from '@/d'; client.create();";
  const config = '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}},"include":["src"]}';
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.app.json': config, 'tsconfig.spec.json': config,
    'src/d.ts': daytona ? "import { Daytona } from '@daytonaio/sdk'; export const client = new Daytona();" :
      "import axios from 'axios'; export const client = axios.create();", 'src/main.ts': source });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['src/main.ts']).toBe(daytona ? source.replace('client.create()', 'client.v0.create()') : source);
  expect(result.code).toBe(0);
});
