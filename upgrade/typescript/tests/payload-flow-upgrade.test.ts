/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';
import { callbackCases, payloadCases } from './payload-flow-cases.js';
import type { Report } from '../src/model.js';

const sdk = "import { Daytona } from '@daytona/sdk';\n";

it.each([
  ...payloadCases.filter(testCase => !testCase.linked).map(testCase => ({ name: testCase.name,
    source: `${sdk}const replacement = { client: {} };
function handler(ctx) { return ctx.client.create(); }
handler(${testCase.payload});\n` })),
  ...callbackCases.filter(testCase => !testCase.linked).map(testCase => ({ name: testCase.name,
    source: `${sdk}import axios from 'axios';
function invoke(factory) { return factory(); }
const make = () => new Daytona();
invoke(${testCase.callback}).create();\n` })),
])('preserves the untyped negative receiver when $name', async ({ source }) => {
  // Given SDK constructions without installed types, including unrelated evidence.
  const root = temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'use.js': source,
  });
  // When the real CLI runs in strict mode.
  const result = await invoke(root, ['--strict', '--json']);
  // Then the only manual item is unresolved SDK types, not a receiver FIXME or rewrite.
  const report: Report = JSON.parse(result.output);
  expect(report.items.map(item => item.kind)).toEqual(['types-unresolved']);
  expect(result.code).toBe(1);
  expect(contents(root)['use.js']).toBe(source);
});
