/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const assignment = "config.apiUrl = 'https://app.daytona.io/api';";

it.each([
  'function connect(config) { ASSIGN new Daytona(config); console.log(config.apiUrl); }',
  'function connect(config) { ASSIGN new Daytona(config); }',
  'function connect({ config }) { ASSIGN new Daytona(config); console.log(config.apiUrl); }',
  'try { work(); } catch (config) { ASSIGN new Daytona(config); console.log(config.apiUrl); }',
  "import { config } from './config.js'; ASSIGN new Daytona(config); console.log(config.apiUrl);",
])('preserves caller-owned URL assignments when declaration is %s', async code => {
  // Given a config whose ownership is not proved by local variable analysis.
  const root = temporaryProject({ 'app.ts': prefix + code.replace('ASSIGN', assignment),
    'config.ts': "export const config = { apiKey: 'key' };" });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then the field survives and the constructor needs manual repair.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain(assignment);
  expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade):');
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'config-field' }));
});
