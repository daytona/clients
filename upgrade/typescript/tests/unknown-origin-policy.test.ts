/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { assertNever } from '../src/model.js';
import { contents, installFakeSdk, invoke as runUpgrade, temporaryProject } from './helpers.js';
import { identityReviewCases, linkedReviewCases, negativeReviewCases } from './unknown-origin-review-cases.js';
import { constructorCallbackReviewCases } from './constructor-callback-cases.js';
import { correlatedCallbackReviewCases } from './correlated-callback-cases.js';
import { accessorMatrixCases, accessorPairMatrixCases } from './accessor-review-cases.js';
import { accessorWriteMatrixCases } from './accessor-write-cases.js';
import { optionsCallbackReviewCases } from './options-callback-cases.js';
import { callbackBindingReviewCases } from './callback-binding-cases.js';

type Outcome = { readonly kind: 'fixme' | 'unchanged' } |
  { readonly kind: 'rewrite'; readonly expected: string; readonly strictCode?: 0 | 1 };
type Case = {
  readonly name: string;
  readonly source: string;
  readonly outcome: Outcome;
  readonly files?: Readonly<Record<string, string>>;
  readonly typed?: boolean;
  readonly path?: string;
};
const fixme = { kind: 'fixme' } as const;
const unchanged = { kind: 'unchanged' } as const;
const sdk = "import { Daytona } from '@daytona/sdk';\n";
const expressPackage = {
  'node_modules/express/package.json': '{"name":"express","main":"index.js"}',
  'node_modules/express/index.js': 'module.exports = function express() { return {}; };',
} as const;
const groups: readonly { readonly name: string; readonly cases: readonly Case[] }[] = [
  { name: 'call-site correlation and accessor selection', cases: [
    ...optionsCallbackReviewCases,
    ...callbackBindingReviewCases,
    ...correlatedCallbackReviewCases, ...accessorMatrixCases, ...accessorPairMatrixCases,
    ...accessorWriteMatrixCases,
  ] },
  { name: 'linked provenance', cases: [
    ...constructorCallbackReviewCases.filter(testCase => testCase.outcome.kind === 'fixme'),
    ...linkedReviewCases,
    { name: 'exported new Daytona without typings', source: "import { client } from './provider';\nclient.create();\n", outcome: fixme,
      files: { 'provider.ts': `${sdk}export const client = new Daytona();` } },
    { name: 'lazy exported binding initialized by assignment', source: "import { client } from './provider';\nclient.create();\n", outcome: fixme,
      files: { 'provider.ts': `${sdk}export let client; export function init() { client = new Daytona(); }` } },
    ...[false, true].map(typed => ({ name: `setter writes exported binding (typed=${typed})`, typed,
      source: "import { client } from './registry.js';\nclient.create();\n", path: 'use.js', outcome: fixme,
      files: { 'registry.js': 'export let client; export function setClient(c) { client = c; }',
        'bootstrap.js': `${sdk}import { setClient } from './registry.js'; setClient(new Daytona());` } })),
    ...[false, true].map(typed => ({ name: `matching exported Map key (typed=${typed})`, typed,
      source: "import { container } from './registry.js';\ncontainer.get('d').create();\n", path: 'use.js', outcome: fixme,
      files: { 'registry.js': 'export const container = new Map();',
        'bootstrap.js': `${sdk}import { container } from './registry.js'; container.set('d', new Daytona());` } })),
    { name: 'local Daytona factory without typings', source: `${sdk}function make() { return new Daytona(); }\nconst client = make();\nclient.create();\n`, outcome: fixme },
    { name: 'imported Daytona factory without typings', source: "import { make } from './factory';\nconst client = make();\nclient.create();\n", outcome: fixme,
      files: { 'factory.ts': `${sdk}export function make() { return new Daytona(); }` } },
    { name: 'linked Daytona type without typings', source: "import { client } from './provider';\nclient.create();\n", outcome: fixme,
      files: { 'provider.ts': "import type { Daytona } from '@daytona/sdk'; export declare const client: Daytona;" } },
  ] },
  { name: 'strong identity', cases: [
    ...identityReviewCases,
    { name: 'unresolved Daytona module token', source: "import { client } from '@/daytona';\nclient.create();\n", outcome: fixme },
    { name: 'unconfigured Daytona path token', source: "import { client } from '@/daytona';\nclient.create();\n", outcome: fixme,
      files: { 'tsconfig.json': '{}' } },
    { name: 'unconfigured Daytona factory path token', source: "import { make } from '@/daytona';\nconst client = make();\nclient.create();\n", outcome: fixme,
      files: { 'tsconfig.json': '{}' } },
    { name: 'global Daytona binding', source: 'daytonaClient.create();\n', outcome: fixme },
    { name: 'Daytona collection binding', source: 'daytonaClients.get(x).create();\n', outcome: fixme },
    { name: 'Daytona-named Map payload', source: 'const daytonaClients = new Map();\ndaytonaClients.get(x).create();\n', outcome: fixme },
    { name: 'Daytona imported symbol survives alias', source: "import { daytonaClient as client } from '@/infra';\nclient.create();\n", outcome: fixme },
    { name: 'Daytona export symbol survives alias', source: "import { client } from './barrel';\nclient.create();\n", outcome: fixme,
      files: { 'barrel.ts': "export { daytonaClient as client } from '@/infra';" } },
    { name: 'unresolved export in Daytona module', source: "import { client } from './daytona';\nclient.create();\n", outcome: fixme,
      files: { 'daytona.ts': 'export const other = 1;' } },
  ] },
  { name: 'generic unresolved', cases: [
    ...constructorCallbackReviewCases.filter(testCase => testCase.outcome.kind === 'unchanged'),
    ...negativeReviewCases,
    { name: 'generic alias client', source: "import { client } from '@/infra';\nclient.create();\n", outcome: unchanged },
    { name: 'generic alias clients', source: "import { clients } from '@/infra';\nclients.get(x).create();\n", outcome: unchanged },
    { name: 'application database lookup', source: "app.get('db').get(k);\n", outcome: unchanged },
    { name: 'request-local cache', source: 'req.app.locals.cache.get(k);\n', outcome: unchanged },
    { name: 'typed Database registry', source: "import { db } from './registry';\ndb.get(k);\n", outcome: unchanged,
      files: { 'registry.ts': 'interface Database { get(k: string): unknown } export let db: Database; export function setDb(value: Database) { db = value; }' } },
    { name: 'Nest injection with any database', source: "import { Inject } from '@nestjs/common';\nclass Service { constructor(@Inject('DB') private db: any) {} run(k: string) { return this.db.get(k); } }\n", outcome: unchanged },
    { name: 'global generic client', source: 'client.create();\n', outcome: unchanged },
    { name: 'arbitrary Daytona string key', source: "map.get('daytona').get(k);\n", outcome: unchanged },
    { name: 'arbitrary Daytona element key', source: 'declare const registry: any;\nconst client = registry["daytona"];\nclient.get(k);\n', outcome: unchanged },
    { name: 'unrelated call to a shared identity factory', typed: true, outcome: unchanged,
      source: `${sdk}function identity(value: any) { return value; }\nidentity(new Daytona());\ndeclare const opaque: any;\nconst client = identity(opaque);\nclient.get(k);\n` },
    { name: 'unrelated same-file Daytona import', source: `${sdk}declare const client: any;\nclient.create();\n`, outcome: unchanged },
    { name: 'unrelated import in provider', source: "import { client } from './provider';\nclient.create();\n", outcome: unchanged,
      files: { 'provider.ts': `${sdk}export const client = unresolved;` } },
    { name: 'wrong exported Map key', typed: true, source: "import { container } from './registry.js';\ncontainer.get('cache').create();\n", path: 'use.js', outcome: unchanged,
      files: { 'registry.js': 'export const container = new Map();',
        'bootstrap.js': `${sdk}import { container } from './registry.js'; container.set('d', new Daytona());` } },
    { name: 'unrelated setter receives Daytona', typed: true, source: "import { client } from './registry.js';\nclient.create();\n", path: 'use.js', outcome: unchanged,
      files: { 'registry.js': 'export let client; let other; export function setOther(c) { other = c; }',
        'bootstrap.js': `${sdk}import { setOther } from './registry.js'; setOther(new Daytona());` } },
  ] },
  { name: 'negative external', cases: [
    ...[false, true].flatMap(installed => [
      { name: `relative Express import (installed=${installed})`, path: 'use.js',
        source: "import { app } from './provider.js';\napp.get('/health', handler);\n", outcome: unchanged,
        files: { ...(installed ? expressPackage : {}), 'provider.js': "import express from 'express'; export const app = express();" } },
      { name: `local Express factory (installed=${installed})`, path: 'use.js',
        source: "import express from 'express';\nfunction make() { return express(); }\nconst app = make();\napp.get('/health', handler);\n", outcome: unchanged,
        files: installed ? expressPackage : {} },
      ...['return express();', 'return cached || express();', 'return (setup(), express());'].map(body => ({
        name: `imported Express factory ${body} (installed=${installed})`, path: 'use.js',
        source: "import { make } from './factory.js';\nconst app = make();\napp.get('/health', handler);\n", outcome: unchanged,
        files: { ...(installed ? expressPackage : {}),
          'factory.js': `import express from 'express'; let cached; export function make() { ${body} }` },
      })),
    ]),
    { name: 'axios before installation', source: "import axios from 'axios';\naxios.get('/health');\n", outcome: unchanged },
    { name: 'lodash before installation', source: "import _ from 'lodash';\n_.get(object, key);\n", outcome: unchanged },
    ...[
      { module: 'axios', type: 'AxiosInstance' },
      { module: '@nestjs/common', type: 'INestApplication' },
      { module: '@prisma/client', type: 'PrismaClient' },
    ].map(({ module, type }) => ({ name: `external ${type} annotation before installation`, outcome: unchanged,
      source: `import type { ${type} } from '${module}';\ndeclare const daytonaClient: ${type};\ndaytonaClient.get(k);\n` })),
    { name: 'request headers in JavaScript route', path: 'use.js', source: "export function GET(request) { return request.headers.get('authorization'); }\n", outcome: unchanged },
    { name: 'external npm origin defeats Daytona binding name', source: "import express from 'express';\nconst daytonaClient = express();\ndaytonaClient.get('/health', handler);\n", outcome: unchanged },
    { name: 'installed untyped npm origin defeats Daytona binding name', path: 'use.js',
      source: "import express from 'express';\nconst daytonaClient = express();\ndaytonaClient.get('/health', handler);\n", files: expressPackage, outcome: unchanged },
    { name: 'external re-export defeats Daytona module token', source: "import { client } from './daytona';\nclient.get('/health');\n", outcome: unchanged,
      files: { 'daytona.ts': "export { default as client } from 'axios';" } },
    { name: 'external factory re-export defeats Daytona module token', source: "import { make } from './daytona';\nconst client = make();\nclient.get('/health');\n", outcome: unchanged,
      files: { 'daytona.ts': "export { default as make } from 'express';" } },
  ] },
  { name: 'proof rewrite', cases: [
    { name: 'typed alias import', typed: true,
      source: "import { client as api } from '@/infra';\napi.create();\n",
      outcome: { kind: 'rewrite', expected: "import { client as api } from '@/infra';\napi.v0.create();\n" },
      files: { 'infra.ts': `${sdk}export const client = new Daytona();` } },
    { name: 'same-file typed new Daytona', typed: true,
      source: `${sdk}const client = new Daytona();\nclient.create();\n`,
      outcome: { kind: 'rewrite', expected: `${sdk}const client = new Daytona();\nclient.v0.create();\n` } },
    { name: 'same-file syntactically proven new Daytona',
      source: `${sdk}const d = new Daytona();\nd.create();\n`,
      outcome: { kind: 'rewrite', expected: `${sdk}const d = new Daytona();\nd.v0.create();\n`, strictCode: 1 } },
  ] },
];

for (const group of groups) describe(group.name, () => {
  it.each(group.cases)('$name', async testCase => {
    // Given isolated project-level SDK evidence, never implicitly receiver evidence.
    const path = testCase.path ?? 'use.ts';
    const root = temporaryProject({
      'package.json': '{"type":"module","dependencies":{"@daytona/sdk":"^2.0.0"}}',
      'tsconfig.json': '{"compilerOptions":{"allowJs":true,"baseUrl":".","paths":{"@/*":["./*"]},"experimentalDecorators":true}}',
      'evidence.ts': sdk,
      ...testCase.files,
      [path]: testCase.source,
    });
    if (testCase.typed) {
      installFakeSdk(root);
      cpSync(join(root, 'node_modules/@daytonaio/sdk'), join(root, 'node_modules/@daytona/sdk'), { recursive: true });
    }
    // When the actual upgrade runs in strict mode.
    const result = await runUpgrade(root, ['--strict', '--json']);
    // Then the outcome is exact, including preservation of every input code byte.
    const after = contents(root)[path];
    switch (testCase.outcome.kind) {
      case 'fixme':
        expect(result.code, result.output).toBe(1);
        expect(after).toMatch(/^[\t ]*\/\/ FIXME\(daytona-upgrade\):[^\r\n]*$/m);
        expect(after?.replace(/^[\t ]*\/\/ FIXME\(daytona-upgrade\):[^\r\n]*\r?\n/gm, '')).toBe(testCase.source);
        break;
      case 'unchanged':
        expect(result.code, result.output).toBe(0);
        expect(after).toBe(testCase.source);
        break;
      case 'rewrite':
        expect(result.code, result.output).toBe(testCase.outcome.strictCode ?? 0);
        expect(after).toBe(testCase.outcome.expected);
        break;
      default:
        assertNever(testCase.outcome);
    }
  });
});
