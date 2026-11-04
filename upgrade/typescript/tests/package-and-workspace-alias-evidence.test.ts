/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'].flatMap(section => [
  { section, name: 'lodash', source: "import _ from 'lodash'; _.get(o, 'a'); _.list();" },
  { section, name: 'axios', source: "import axios from 'axios'; axios.get('/'); axios.delete('/'); axios.create();" },
  { section, name: '@vendor/http', source: "import http from '@vendor/http/client'; http.get('/');" },
]))('keeps uninstalled $name clean when declared in $section', async ({ section, name, source }) => {
  // Given a declared npm dependency, even with a matching alias/workspace name.
  const root = temporaryProject({
    'package.json': JSON.stringify({ workspaces: ['packages/*'], dependencies: { '@daytonaio/sdk': '0.224.0' } }),
    'packages/other/package.json': JSON.stringify({ [section]: { [name]: '*' } }),
    'packages/http/package.json': JSON.stringify({ name }),
    'tsconfig.json': JSON.stringify({ compilerOptions: { baseUrl: '.', paths: { [name]: ['missing'], [`${name}/*`]: ['missing/*'] } } }),
    'index.ts': source,
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
});

it.each(['lodash', 'axios', 'axios/client'])('keeps undeclared bare npm specifiers clean without node_modules: %s', async name => {
  // Given no configured project alias (a catch-all does not count).
  const source = `import api from '${name}'; api.get('/'); api.create();`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"*":["missing/*"]}}}', 'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});

it.each(['infra', '@/infra', '@workspace/infra'])('leaves generic configured or workspace aliases clean: %s', async name => {
  // Given a recognized but unavailable project module.
  const root = temporaryProject({ 'package.json': '{"workspaces":["packages/*"],"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'packages/infra/package.json': '{"name":"@workspace/infra"}',
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"infra":["missing"],"@/*":["missing/*"]}}}',
    'index.ts': `import { client } from '${name}'; client.create();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(`import { client } from '${name}'; client.create();`);
  expect(result.code).toBe(0);
});
