/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const sdk = "import { Daytona } from '@daytona/sdk';\nimport axios from 'axios';\n";
const fixme = { kind: 'fixme' } as const;
const unchanged = { kind: 'unchanged' } as const;

export const objectReceiverCases = [
  { name: 'last external duplicate', object: '{ daytona: new Daytona(), daytona: axios.create() }', review: false },
  { name: 'last literal duplicate', object: '{ daytona: new Daytona(), daytona: {} }', review: false },
  { name: 'last Daytona duplicate', object: '{ daytona: axios.create(), daytona: new Daytona() }', review: true },
  { name: 'two Daytona definitions', object: '{ daytona: new Daytona(), daytona: new Daytona() }', review: true },
  { name: 'last opaque duplicate', object: '{ daytona: new Daytona(), daytona: opaque }', review: true },
  { name: 'later opaque spread', object: '{ daytona: new Daytona(), ...overrides }', review: true },
  { name: 'later external spread', object: '{ daytona: new Daytona(), ...{ daytona: axios.create() } }', review: false },
  { name: 'later computed replacement', object: '{ daytona: new Daytona(), [key]: opaque }', review: true },
  { name: 'quoted duplicate', object: '{ daytona: new Daytona(), "daytona": axios.create() }', review: false },
  { name: 'computed literal duplicate', object: '{ daytona: new Daytona(), ["daytona"]: axios.create() }', review: false },
  { name: 'spread-seeded later replacement', object: '{ ...{ daytona: new Daytona() }, ...overrides }', review: true },
  { name: 'spread-seeded definite external', object: '{ ...{ daytona: new Daytona() }, daytona: axios.create() }', review: false },
] as const;

export const callbackReceiverCases = [
  { name: 'inline arrow', setup: 'function withClient(cb) { return cb(new Daytona()); }',
    use: 'withClient(c => c.create());', review: true },
  { name: 'inline function', setup: 'function withClient(cb) { return cb(new Daytona()); }',
    use: 'withClient(function (c) {\n  return c.create();\n});', review: true },
  { name: 'named callback', setup: 'function withClient(cb) { return cb(new Daytona()); }',
    use: 'function consume(c) {\n  return c.create();\n}\nwithClient(consume);', review: true },
  { name: 'second parameter', setup: 'function withClient(cb) { return cb({}, new Daytona()); }',
    use: 'withClient((other, c) => c.create());', review: true },
  { name: 'forwarded callback', setup: 'function withClient(cb) { return cb(new Daytona()); }\nfunction forward(cb) { return withClient(cb); }',
    use: 'forward(c => c.create());', review: true },
  { name: 'external argument', setup: 'function withClient(cb) { return cb(axios.create()); }',
    use: 'withClient(c => c.create());', review: false },
  { name: 'opaque argument', setup: 'function withClient(cb) { return cb(opaque); }',
    use: 'withClient(c => c.create());', review: false },
  { name: 'unrelated argument', setup: 'function withClient(cb) { return cb({}, new Daytona()); }',
    use: 'withClient(c => c.create());', review: false },
  { name: 'unrelated callback', setup: 'function withClient(cb, other) { other(new Daytona()); return cb(opaque); }',
    use: 'withClient(c => c.create(), () => {});', review: false },
] as const;

export const receiverReviewCases = [false, true].flatMap(typed => [
  ...objectReceiverCases.flatMap(testCase => ['deps.daytona', 'deps["daytona"]', 'client'].map(receiver => ({
    name: `${receiver} (typed=${typed}) object ${testCase.name}`, typed, path: 'use.js',
    outcome: testCase.review ? fixme : unchanged,
    source: `${sdk}const deps = ${testCase.object};\n${receiver === 'client' ? 'const client = deps.daytona;\n' : ''}${receiver}.get();\n` }))),
  ...callbackReceiverCases.map(testCase => ({ name: `callback receiver ${testCase.name} (typed=${typed})`, typed, path: 'use.js',
    outcome: testCase.review ? fixme : unchanged,
    source: `${sdk}${testCase.setup}\n${testCase.use}\n` })),
].filter(testCase => typed || testCase.outcome.kind === 'fixme'));
