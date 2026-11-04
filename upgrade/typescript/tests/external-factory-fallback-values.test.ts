/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([false, true].flatMap(installed => [
  'return cached || express();', 'return cached ?? express();', 'return cached ??= express();',
  'return cached ||= express();', 'return flag ? express() : express();',
].map(body => ({ installed, body }))))('keeps mixed-SDK cached Express clean (installed=$installed): $body', async ({ installed, body }) => {
  // Given a bootstrap importing both SDK and Express, with an uninitialised cache.
  const routes = "import { app } from './app.js'; app.get('/a', handler); app.get('/b', handler); app.delete('/c', handler);";
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0","express":"*"}}',
    'factory.js': `import express from 'express'; import { Daytona } from '@daytonaio/sdk'; let cached; export function make() { ${body} }`,
    'app.js': "import { make } from './factory.js'; export const app = make();", 'routes.js': routes,
    ...(installed ? { 'node_modules/express/package.json': '{"main":"index.js"}',
      'node_modules/express/index.js': 'module.exports = function express() { return {}; };' } : {}),
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then unknown SDK types elsewhere do not turn external routes into review items.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'routes.js' && item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['routes.js']).toBe(routes);
  expect(result.code).toBe(0);
});
