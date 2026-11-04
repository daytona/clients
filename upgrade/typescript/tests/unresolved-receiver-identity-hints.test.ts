/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each([
  { name: 'lodash without types', source: "import _ from 'lodash'; _.get(o, 'a.b');",
    files: { 'node_modules/lodash/package.json': '{"main":"index.js"}', 'node_modules/lodash/index.js': 'module.exports = { get() {} };' } },
  { name: 'axios with untyped installed package', source: "import axios from 'axios'; axios.get('/'); axios.delete('/');",
    files: { 'node_modules/axios/package.json': '{"main":"index.js"}', 'node_modules/axios/index.js': 'module.exports = external;' } },
  { name: 'untyped npm Express app', source: "import app from 'express-app';\n" +
    Array.from({ length: 25 }, (_, index) => `app.get('/route-${index}', handler);`).join('\n'),
    files: { 'node_modules/express-app/package.json': '{"main":"index.js"}', 'node_modules/express-app/index.js': 'module.exports = require("express")();' } },
  { name: 'nested database services', source: "import db from '@/db'; db.user.create(); db.session.delete();", files: {} },
  { name: 'nested destructuring', source: "import db from '@/db'; const { user: { api } } = db; api.create();", files: {} },
  { name: 'property alias', source: "import db from '@/db'; const api = db.user; api.create();", files: {} },
  { name: 'plain alias', source: "import api from '@/api'; const local = api; local.create();", files: {} },
])('leaves unrelated receivers untouched when using $name', async ({ source, files }) => {
  // Given unrelated code in a project that also depends on Daytona.
  const root = temporaryProject({ ...files, 'package.json': manifest,
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"],"@app/*/client":["src/*"],"@app/*":["src/*"]}}}',
    'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then no hints, edits, or strict-mode failure are introduced into the unrelated source.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});

it.each(['./missing', '../missing', '@/missing', '@app/infra/client', '@workspace/infra', '@workspace/infra/client', '@app/infra']
  .flatMap(module => [
    `import { api } from '${module}'; api.create();`,
    `import api from '${module}'; api.create();`,
    `import * as api from '${module}'; api.create();`,
    `import api = require('${module}'); api.create();`,
    `import infra from '${module}'; const { api } = infra; api.create();`,
    `import infra from '${module}'; const { api: local } = infra; local.create();`,
  ]))('leaves generic unresolved project-local receivers unchanged: %s', async source => {
  // Given recognized local specifiers, without receiver-linked Daytona evidence.
  const root = temporaryProject({ 'package.json': '{"workspaces":["packages/*"],"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'packages/infra/package.json': '{"name":"@workspace/infra"}',
    'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"],"@app/*/client":["src/*"],"@app/*":["src/*"]}}}',
    'index.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then missing local modules and generic names do not establish receiver evidence.
  const report: Report = JSON.parse(result.output);
  expect(report.items).toEqual([]);
  expect(contents(root)['index.ts']).toBe(source);
  expect(result.code).toBe(0);
});
