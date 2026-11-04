/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const fixme = { kind: 'fixme' } as const;
const unchanged = { kind: 'unchanged' } as const;
const sdk = "import { Daytona } from '@daytona/sdk';\n";

export const payloadCases = [
  { name: 'plain property', payload: '{ client: new Daytona() }', linked: true, origin: 'unknown' },
  { name: 'later opaque spread', payload: '{ client: new Daytona(), ...overrides }', linked: true, origin: 'unknown' },
  { name: 'earlier opaque spread', payload: '{ ...overrides, client: new Daytona() }', linked: true, origin: 'unknown' },
  { name: 'duplicate opaque property', payload: '{ client: new Daytona(), client: opaque }', linked: true, origin: 'unknown' },
  { name: 'ambiguous computed property', payload: '{ client: new Daytona(), [key]: opaque }', linked: true, origin: 'unknown' },
  { name: 'duplicate non-client property', payload: '{ client: new Daytona(), client: {} }', linked: false, origin: 'not-daytona' },
  { name: 'later literal non-client spread', payload: '{ client: new Daytona(), ...{ client: {} } }', linked: false, origin: 'not-daytona' },
  { name: 'later aliased non-client spread', payload: '{ client: new Daytona(), ...replacement }', linked: false, origin: 'not-daytona' },
  { name: 'unrelated literal spread', payload: '{ client: new Daytona(), ...{ other: opaque } }', linked: true, origin: 'unknown' },
  { name: 'spread-only client', payload: '{ ...{ client: new Daytona() } }', linked: true, origin: 'unknown' },
  { name: 'unrelated Daytona property', payload: '{ other: new Daytona(), client: opaque }', linked: false, origin: 'unknown' },
] as const;

// Untyped negative fixtures also report unresolved SDK types in bootstrap.js;
// their unchanged-code contract is covered separately from the matrix's strict exit-0 rows.
export const payloadReviewCases = [false, true].flatMap(typed => payloadCases.filter(testCase => typed || testCase.linked).flatMap(testCase =>
  ['client', 'daytona'].map(key => ({
  name: `payload ${testCase.name} at ${key} (typed=${typed})`, typed, path: 'use.js',
  outcome: testCase.linked || key === 'daytona' && testCase.origin === 'unknown' ? fixme : unchanged,
  source: `export function handler(ctx) {\n  return ctx.${key}.create();\n}\n`,
  files: { 'bootstrap.js': `${sdk}import { handler } from './use.js';
    const replacement = { ${key}: {} };
    handler(${testCase.payload.replaceAll('client', key)});` },
}))));

export const callbackCases = [
  { name: 'arrow callback', callback: '() => new Daytona()', linked: true, origin: 'unknown' },
  { name: 'block callback', callback: '() => { return new Daytona(); }', linked: true, origin: 'unknown' },
  { name: 'function callback', callback: 'function () { return new Daytona(); }', linked: true, origin: 'unknown' },
  { name: 'aliased callback', callback: 'make', linked: true, origin: 'unknown' },
  { name: 'conditional callback', callback: 'flag ? make : opaque', linked: true, origin: 'unknown' },
  { name: 'opaque callback', callback: 'opaque', linked: false, origin: 'unknown' },
  { name: 'opaque-return callback', callback: '() => opaque', linked: false, origin: 'unknown' },
  { name: 'non-client callback', callback: '() => ({})', linked: false, origin: 'not-daytona' },
  { name: 'external callback', callback: '() => axios', linked: false, origin: 'not-daytona' },
] as const;

export const callbackReviewCases = [false, true].flatMap(typed => callbackCases.filter(testCase => typed || testCase.linked).map(testCase => ({
  name: `called parameter ${testCase.name} (typed=${typed})`, typed, path: 'use.js',
  outcome: testCase.linked ? fixme : unchanged,
  source: `${sdk}import axios from 'axios';
function invoke(factory) { return factory(); }
const make = () => new Daytona();
invoke(${testCase.callback}).create();
`,
})));

export const importedCallbackReviewCases = [false, true].map(typed => ({
  name: `imported higher-order factory (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
  source: `${sdk}import { invoke } from './factory.js';\ninvoke(() => new Daytona()).create();\n`,
  files: { 'factory.js': 'export function invoke(factory) { return factory(); }' },
}));

export const callbackIdentityReviewCases = [false, true].map(typed => ({
  name: `opaque callback return with Daytona identity (typed=${typed})`, typed, path: 'use.js', outcome: fixme,
  source: `function invoke(factory) { return factory(); }
const daytonaClient = invoke(() => opaque);
daytonaClient.create();
`,
}));
