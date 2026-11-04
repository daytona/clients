/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"workspaces":["packages/*"],"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each([
  { name: 'ESM Express default', provider: "import express from 'express'; export default express();", consumer: "import app from './app.js';" },
  { name: 'ESM Express named', provider: "import express from 'express'; export const app = express();", consumer: "import { app } from './app.js';" },
  { name: 'CJS Express default', provider: "const express = require('express'); module.exports = express();", consumer: "const app = require('./app.js');" },
  { name: 'CJS Express through ESM', provider: "const express = require('express'); module.exports = express();", consumer: "import app from './app.js';" },
  { name: 'CJS named export', provider: "const express = require('express'); exports.app = express();", consumer: "const { app } = require('./app.js');" },
  { name: 'literal', provider: 'export default { get: missing, delete: missing };', consumer: "import app from './app.js';" },
  { name: 'another package import', provider: "export { default } from 'express';", consumer: "import app from './app.js';" },
  { name: 'forwarded package import', provider: "import express from 'express'; export default express;", consumer: "import app from './app.js';" },
  { name: 'named re-export', provider: "export { app as default } from './server.js';", consumer: "import app from './app.js';" },
  { name: 'star re-export', provider: "export * from './server.js';", consumer: "import { app } from './app.js';" },
  { name: 'project import alias', provider: "import { app } from './server.js'; export default app;", consumer: "import app from './app.js';" },
  { name: 'missing export in resolved module', provider: 'export const other = 1;', consumer: "import app from './app.js';" },
])('leaves routes untouched when the exporting value is $name', async ({ provider, consumer }) => {
  // Given routes with untyped project-local, non-Daytona provenance.
  const source = `${consumer}\napp.get('/a', handler); app.get('/b', handler); app.delete('/c', handler);`;
  const root = temporaryProject({ 'package.json': manifest, 'src/app.js': provider,
    'src/server.js': "import express from 'express'; export const app = express();", 'src/routes.js': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then neither manual items nor edits are introduced into the routes.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toEqual([]);
  expect(contents(root)['src/routes.js']).toBe(source);
  expect(result.code).toBe(0);
});

it('leaves axios calls untouched when a workspace package exports the instance', async () => {
  // Given a workspace package resolved through the owning paths mapping.
  const source = "import http from '@workspace/http'; http.get('/'); http.delete('/');";
  const root = temporaryProject({ 'package.json': manifest,
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@workspace/http":["packages/http/index.js"]}}}',
    'packages/http/package.json': '{"name":"@workspace/http","main":"index.js"}',
    'node_modules/axios/package.json': '{"main":"index.js"}', 'node_modules/axios/index.js': 'module.exports = external;',
    'packages/http/index.js': "import axios from 'axios'; export default axios.create();", 'index.js': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.js']).toBe(source);
  expect(result.code).toBe(0);
});

it.each([true, false].flatMap(typed => [
  { typed, provider: "export const api = new Daytona();", consumer: "import { api } from './local';" },
  { typed, provider: 'export default new Daytona();', consumer: "import api from './local';" },
  { typed, provider: "export { api } from './origin';", consumer: "import { api } from './local';" },
  { typed, provider: "import { api } from './origin'; export { api };", consumer: "import { api } from './local';" },
  { typed, provider: "export const api = new Daytona();", consumer: "import * as infra from './local'; const { api } = infra;" },
  { typed, provider: "export declare const api: Daytona;", consumer: "import { api } from './local';" },
]))('keeps Daytona behavior through an exported client (typed=$typed): $provider', async ({ typed, provider, consumer }) => {
  // Given an actual SDK client behind default/named exports or a re-export.
  const root = temporaryProject({ 'package.json': manifest,
    'local.ts': `import { Daytona } from '@daytonaio/sdk'; ${provider}`,
    'origin.ts': "import { Daytona } from '@daytonaio/sdk'; export const api = new Daytona();",
    'index.ts': `${consumer} api.create();` });
  if (typed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then typed receivers migrate; untyped receivers retain a review item, never silence.
  const report: Report = JSON.parse(result.output);
  expect(contents(root)['index.ts']).toContain(typed ? 'api.v0.create()' : 'FIXME(daytona-upgrade)');
  expect(report.items.filter(item => item.file === 'index.ts' && item.kind === 'fixme')).toHaveLength(typed ? 0 : 1);
  expect(result.code).toBe(typed ? 0 : 1);
});

it('terminates cyclic project re-exports without inventing client provenance', async () => {
  // Given an unresolved value behind a cycle of resolvable project files.
  const source = "import { api } from './a'; api.create();";
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': source,
    'a.ts': "export * from './b';", 'b.ts': "export * from './a';" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});

it('leaves a missing generic project import unchanged without receiver evidence', async () => {
  // Given an unresolved relative import without Daytona identity or linked flow.
  const root = temporaryProject({ 'package.json': manifest, 'index.js': "import api from './missing.js'; api.create();" });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(contents(root)['index.js']).toBe("import api from './missing.js'; api.create();");
  expect(result.code).toBe(0);
});
