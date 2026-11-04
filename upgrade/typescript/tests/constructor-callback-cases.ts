/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const sdk = "import { Daytona } from '@daytona/sdk';\nimport axios from 'axios';\n";
const fixme = { kind: 'fixme' } as const;
const unchanged = { kind: 'unchanged' } as const;

export const constructorCallbackCases = [
  { name: 'inline constructor callback', setup: 'class Wrapper { constructor(cb) { cb(new Daytona()); } }',
    use: 'new Wrapper(c => c.create());', review: true },
  { name: 'function constructor callback', setup: 'class Wrapper { constructor(cb) { cb(new Daytona()); } }',
    use: 'new Wrapper(function (c) {\n  return c.create();\n});', review: true },
  { name: 'named constructor callback', setup: 'class Wrapper { constructor(cb) { cb(new Daytona()); } }',
    use: 'function consume(c) {\n  return c.create();\n}\nnew Wrapper(consume);', review: true },
  { name: 'second constructor callback slot', setup: 'class Wrapper { constructor(other, cb) { cb({}, new Daytona()); } }',
    use: 'new Wrapper({}, (other, c) => c.create());', review: true },
  { name: 'aliased class expression callback', setup: 'const Wrapper = class { constructor(cb) { cb(new Daytona()); } };\nconst Alias = Wrapper;',
    use: 'new Alias(c => c.create());', review: true },
  { name: 'forwarded constructor callback', setup: 'class Wrapper { constructor(cb) { cb(new Daytona()); } }\nfunction forward(cb) { return new Wrapper(cb); }',
    use: 'forward(c => c.create());', review: true },
  { name: 'direct constructor parameter', setup: 'class Wrapper { constructor(c) {\n  c.create();\n} }',
    use: 'new Wrapper(new Daytona());', review: true },
  { name: 'external constructor callback argument', setup: 'class Wrapper { constructor(cb) { cb(axios.create()); } }',
    use: 'new Wrapper(c => c.create());', review: false },
  { name: 'opaque constructor callback argument', setup: 'class Wrapper { constructor(cb) { cb(opaque); } }',
    use: 'new Wrapper(c => c.create());', review: false },
  { name: 'unrelated constructor callback argument', setup: 'class Wrapper { constructor(cb) { cb({}, new Daytona()); } }',
    use: 'new Wrapper(c => c.create());', review: false },
  { name: 'unrelated constructor callback slot', setup: 'class Wrapper { constructor(cb, other) { other(new Daytona()); cb(opaque); } }',
    use: 'new Wrapper(c => c.create(), () => {});', review: false },
] as const;

export const constructorCallbackReviewCases = [false, true].flatMap(typed => constructorCallbackCases
  .filter(testCase => typed || testCase.review).map(testCase => ({
    name: `${testCase.name} (typed=${typed})`, typed, path: 'use.js',
    outcome: testCase.review ? fixme : unchanged,
    source: `${sdk}${testCase.setup}\n${testCase.use}\n`,
  })));
