/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  'const make = flag ? express : express;',
  'let cached; const make = cached || express;',
  'let cached; const make = cached ?? express;',
  'let make; make = express;',
  'let make; make ||= express;',
  'let make; make ??= express;',
])('preserves external-call origins through factory aliases: %s', async alias => {
  // Given an external factory alias and unrelated SDK evidence elsewhere in the project.
  const routes = "import { app } from './app.js'; app.get('/a', handler); app.delete('/b', handler);";
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0","express":"*"}}',
    'main.js': "import { Daytona } from '@daytonaio/sdk';",
    'factory.js': `import express from 'express'; ${alias} export function build() { return make(); }`,
    'app.js': "import { build } from './factory.js'; export const app = build();", 'routes.js': routes });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then unrelated routes stay clean instead of receiving project-wide false FIXMEs.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.file === 'routes.js' && item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['routes.js']).toBe(routes);
  expect(result.code).toBe(0);
});
