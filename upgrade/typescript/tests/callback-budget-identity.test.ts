/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

it.each([false, true])('does not invent SDK identity on budget exhaustion with dependency=%s', async dependency => {
  // Given only unrelated clients, enough callers to exhaust analysis, and no SDK import.
  const root = temporaryProject({
    'app.js': `import axios from 'axios';\nfunction run(client) { client.get('/health'); }\n${'run(axios);\n'.repeat(6_000)}`,
    'package.json': JSON.stringify({ dependencies: dependency ? { '@daytona/sdk': '0.224.0' } : {} }),
  });
  // When
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then manifest membership alone cannot turn an incomplete search into a FIXME.
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(result.code).toBe(dependency ? 0 : 2);
});
