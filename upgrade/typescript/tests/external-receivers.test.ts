/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { upgrade } from '../src/upgrade.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const external = [
  "import { createClient } from 'redis'; const redisClient = createClient();",
  "import Redis from 'ioredis'; const redisClient = new Redis();",
  "import { Redis as Cache } from '@vendor/cache'; const redis_client = new Cache();",
  "import * as redis from 'redis'; const redisClient = redis.createClient();",
  "import redis from 'redis'; const redisClient = new redis.Redis();",
] as const;

it.each(external)('produces no migration items for an external receiver: %s', declaration => {
  // Given an in-memory project without dependency types.
  const project = new Project({ useInMemoryFileSystem: true });
  const name = declaration.includes('redis_client') ? 'redis_client' : 'redisClient';
  const source = project.createSourceFile('/index.ts', `import { Daytona } from '@daytona/sdk';\n${declaration}\n${name}.get(k); ${name}.del(k);`);
  // When
  const result = upgrade(source, loadMapping(), { root: '/', dropApiUrl: false });
  // Then
  expect(result.items).toEqual([]);
  expect(result.after).toBe(source.getFullText());
});

it.each(external.flatMap(declaration => ['@daytona/sdk', '@daytonaio/sdk'].map(module => ({ declaration, module }))))(
  'exits strict mode successfully without dependency types from $module: $declaration', async ({ declaration, module }) => {
  // Given
  const name = declaration.includes('redis_client') ? 'redis_client' : 'redisClient';
  const source = `import { Daytona } from '${module}';\n${declaration}\n${name}.get(k); ${name}.del(k);`;
  const root = temporaryProject({
    'package.json': JSON.stringify({ dependencies: { [module]: module === '@daytona/sdk' ? '^2.0.0' : '0.224.0' } }),
    'index.ts': source,
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source.replace('@daytonaio/sdk', '@daytona/sdk'));
});

it.each([
  "import { createClient } from './local'; const redisClient = createClient();",
  "const redisClient = createClient();",
  "import { createClient } from '@daytona/sdk'; const redisClient = createClient();",
  "import { createClient } from '@daytonaio/sdk'; const redisClient = createClient();",
  "import { createClient } from '#local'; const redisClient = createClient();",
  "import { createClient } from 'redis'; function work(createClient) { const redisClient = createClient(); redisClient.get(k); }",
  "import { createClient } from 'redis'; let redisClient = createClient(); redisClient = unknownClient;",
].map(declaration => ({ declaration, fixmes: declaration.includes("from '@daytona") ? 1 : 0 })))
('requires linked SDK evidence for project-local or unresolved receiver: $declaration', async ({ declaration, fixmes }) => {
  // Given
  const call = declaration.includes('function work') ? '' : 'redisClient.get(k);';
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytona/sdk';\n${declaration}\n${call}` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(fixmes);
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toHaveLength(fixmes);
  expect(contents(root)['index.ts']).not.toContain('redisClient.v0.get(k)');
});

it.each([true, false])('leaves generic project factories through a tsconfig path clean, resolved=%s', async resolved => {
  // Given
  const root = temporaryProject({
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"factories":["local.ts"]}}}',
    ...(resolved ? { 'local.ts': 'export declare function createClient(): unknown;' } : {}),
    'index.ts': "import { Daytona } from '@daytona/sdk'; import { createClient } from 'factories'; const redisClient = createClient(); redisClient.get(k);",
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['index.ts']).toContain('redisClient.get(k);');
  expect(contents(root)['index.ts']).not.toContain('redisClient.v0.');
});

it('migrates an external factory whose declarations return a Daytona client', () => {
  // Given a dependency without the SDK's types whose factory declares a Daytona result.
  const project = new Project({ useInMemoryFileSystem: true });
  project.createSourceFile('/node_modules/sandbox-factory/index.d.ts',
    "import type { Daytona } from '@daytona/sdk';\nexport declare function makeClient(): Daytona;");
  project.createSourceFile('/node_modules/sandbox-factory/package.json', '{"name":"sandbox-factory","types":"index.d.ts"}');
  const source = project.createSourceFile('/index.ts',
    "import { Daytona } from '@daytona/sdk';\nimport { makeClient } from 'sandbox-factory';\nconst sandboxClient = makeClient();\nsandboxClient.create();");
  // When
  const result = upgrade(source, loadMapping(), { root: '/', dropApiUrl: false });
  // Then the declared return type wins over the external provenance.
  expect(result.after).toContain('sandboxClient.v0.create()');
});
