/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const sdk = "import { Daytona } from '@daytona/sdk';\nimport axios from 'axios';\n";

export const callbackBindingCases = [
  ...[
    'const { onReady } = opts; return onReady(new Daytona());',
    'const { onReady: callback } = opts; return callback(new Daytona());',
    'const alias = opts; const { onReady } = alias; return onReady(new Daytona());',
  ].map(body => ({ name: `local destructuring ${body}`, body,
    use: 'ready({ onReady: c => c.create() });', review: true })),
  ...['opts.onReady', "opts['onReady']"].flatMap(target => [
    { name: `overwritten original ${target}`,
      body: `${target} = () => {}; return ${target}(new Daytona());`,
      use: 'ready({ onReady: c => c.create() });', review: false },
    { name: `replacement receives Daytona ${target}`,
      body: `${target} = c => c.create(); return ${target}(new Daytona());`,
      use: 'ready({ onReady: () => {} });', review: true },
    { name: `replacement receives external ${target}`,
      body: `${target} = c => c.create(); return ${target}(axios.create());`,
      use: 'ready({ onReady: () => {} });', review: false },
  ]),
  { name: 'local destructuring after replacement',
    body: 'opts.onReady = c => c.create(); const { onReady } = opts; return onReady(new Daytona());',
    use: 'ready({ onReady: () => {} });', review: true },
  { name: 'unrelated property write',
    body: 'opts.onOther = () => {}; return opts.onReady(new Daytona());',
    use: 'ready({ onReady: c => c.create() });', review: true },
  { name: 'local destructuring invocation correlation',
    body: 'const { onReady } = opts; return onReady(value);',
    use: 'ready({ onReady: () => {} }, new Daytona());\nready({ onReady: c => c.create() }, axios.create());', review: false },
  { name: 'written callback invocation correlation',
    body: 'opts.onReady = replacement; return opts.onReady(value);',
    use: 'function replacement(c) {\n  return c.create();\n}\nready({ onReady: () => {} }, new Daytona());', review: true },
] as const;

export const callbackBindingReviewCases = [false, true].flatMap(typed => callbackBindingCases
  .filter(testCase => typed || testCase.review).map(testCase => ({
  name: `${testCase.name} (typed=${typed})`, typed, path: 'use.js',
  source: `${sdk}function ready(opts, value) {\n${testCase.body}\n}\n${testCase.use}\n`,
  outcome: testCase.review ? { kind: 'fixme' } as const : { kind: 'unchanged' } as const,
})));
