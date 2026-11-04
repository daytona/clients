/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const sdk = "import { Daytona } from '@daytona/sdk';\nimport axios from 'axios';\n";
const fixme = { kind: 'fixme' } as const;
const unchanged = { kind: 'unchanged' } as const;
const wrappers = [
  { name: 'function', setup: 'function withResource(value, cb) { return cb(value); }', invoke: 'withResource' },
  { name: 'constructor', setup: 'class Wrapper { constructor(value, cb) { cb(value); } }', invoke: 'new Wrapper' },
  { name: 'factory', setup: 'const withResource = (value, cb) => cb(value);', invoke: 'withResource' },
  { name: 'forwarded function', setup: 'function forward(value, cb) { return cb(value); }\nfunction withResource(value, cb) { return forward(value, cb); }', invoke: 'withResource' },
  { name: 'forwarded constructor', setup: 'class Wrapper { constructor(value, cb) { cb(value); } }\nfunction withResource(value, cb) { return new Wrapper(value, cb); }', invoke: 'withResource' },
  { name: 'factory return argument', setup: 'function identity(value) { return value; }\nfunction withResource(value, cb) { return cb(identity(value)); }', invoke: 'withResource' },
] as const;

export const correlatedCallbackCases = wrappers.flatMap(wrapper => [false, true].map(review => ({
  name: `${wrapper.name} ${review ? 'Daytona' : 'external'} callback`, review,
  source: `${sdk}${wrapper.setup}\n${wrapper.invoke}(${review ? 'axios.create()' : 'new Daytona()'}, () => {});\n${wrapper.invoke}(${review ? 'new Daytona()' : 'axios.create()'}, c => c.get('/health'));\n`,
})));

export const correlatedCallbackReviewCases = [false, true].flatMap(typed => correlatedCallbackCases.map(testCase => ({
  name: `${testCase.name} (typed=${typed})`, typed, path: 'use.js', source: testCase.source,
  outcome: testCase.review ? fixme : unchanged,
})).filter(testCase => typed || testCase.outcome.kind === 'fixme'));
