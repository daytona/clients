/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const sdk = "import { Daytona } from '@daytona/sdk';\nimport axios from 'axios';\n";
const fixme = { kind: 'fixme' } as const;
const unchanged = { kind: 'unchanged' } as const;
const wrappers = [
  { name: 'dot member', setup: 'function ready(opts) { return opts.onReady(new Daytona()); }' },
  { name: 'literal member', setup: "function ready(opts) { return opts['onReady'](new Daytona()); }" },
  { name: 'destructured parameter', setup: 'const ready = ({ onReady }) => onReady(new Daytona());' },
  { name: 'renamed destructured parameter', setup: 'function ready({ onReady: callback }) { return callback(new Daytona()); }' },
  { name: 'forwarded options', setup: 'function forward(opts) { return opts.onReady(new Daytona()); }\nfunction ready(opts) { return forward(opts); }' },
] as const;

export const optionsCallbackCases = [
  ...wrappers.map(wrapper => ({ name: wrapper.name, setup: wrapper.setup,
    use: 'ready({ onReady: c => c.create() });', review: true })),
  { name: 'named options callback', setup: 'function ready(opts) { return opts.onReady(new Daytona()); }',
    use: 'function consume(c) {\n  return c.create();\n}\nready({ onReady: consume });', review: true },
  ...['opts.onReady', "opts['onReady']", 'onReady'].flatMap(callee => [false, true].map(review => ({
    name: `${callee} ${review ? 'Daytona' : 'external'} invocation correlation`,
    setup: callee === 'onReady' ? 'function ready(value, { onReady }) { return onReady(value); }' :
      `function ready(value, opts) { return ${callee}(value); }`,
    use: `ready(${review ? 'axios.create()' : 'new Daytona()'}, { onReady: () => {} });\nready(${review ? 'new Daytona()' : 'axios.create()'}, { onReady: c => c.create() });`, review,
  }))),
  ...[false, true].map(review => ({ name: `forwarded options ${review ? 'Daytona' : 'external'} invocation correlation`,
    setup: 'function forward(value, opts) { return opts.onReady(value); }\nfunction ready(value, opts) { return forward(value, opts); }',
    use: `ready(${review ? 'axios.create()' : 'new Daytona()'}, { onReady: () => {} });\nready(${review ? 'new Daytona()' : 'axios.create()'}, { onReady: c => c.create() });`, review })),
  { name: 'external options callback argument', setup: 'function ready(opts) { return opts.onReady(axios.create()); }',
    use: 'ready({ onReady: c => c.create() });', review: false },
  { name: 'different options property', setup: 'function ready(opts) { return opts.onOther(new Daytona()); }',
    use: 'ready({ onReady: c => c.create(), onOther: () => {} });', review: false },
  { name: 'spread-only options property', setup: 'function ready(opts) { return opts.onReady(new Daytona()); }',
    use: 'ready({ ...{ onReady: c => c.create() } });', review: false },
  { name: 'potentially replacing options spread', setup: 'function ready(opts) { return opts.onReady(new Daytona()); }',
    use: 'ready({ onReady: c => c.create(), ...opaque });', review: false },
  { name: 'potentially replacing computed property', setup: 'function ready(opts) { return opts.onReady(new Daytona()); }',
    use: 'ready({ onReady: c => c.create(), [key]: () => {} });', review: false },
  { name: 'duplicate options property', setup: 'function ready(opts) { return opts.onReady(new Daytona()); }',
    use: 'ready({ onReady: c => c.create(), onReady: () => {} });', review: false },
  { name: 'different destructured property', setup: 'function ready({ onOther }) { return onOther(new Daytona()); }',
    use: 'ready({ onReady: c => c.create(), onOther: () => {} });', review: false },
  { name: 'shared callback with mixed invocations', setup: 'function ready(value, opts) { return opts.onReady(value); }',
    use: 'const consume = c => c.create();\nready(axios.create(), { onReady: consume });\nready(new Daytona(), { onReady: consume });', review: true },
] as const;

export const optionsCallbackReviewCases = [false, true].flatMap(typed => optionsCallbackCases
  .filter(testCase => typed || testCase.review).map(testCase => ({
    name: `options callback ${testCase.name} (typed=${typed})`, typed, path: 'use.js',
    source: `${sdk}${testCase.setup}\n${testCase.use}\n`, outcome: testCase.review ? fixme : unchanged,
  })));
