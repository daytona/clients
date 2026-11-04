/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const sdk = "import { Daytona } from '@daytona/sdk';\nimport axios from 'axios';\n";

export const accessorReviewCases = [
  { name: 'external getter replaces data', object: '{ daytona: new Daytona(), get daytona() { return axios.create(); } }', review: false },
  { name: 'literal getter replaces data', object: '{ daytona: new Daytona(), get daytona() { return {}; } }', review: false },
  { name: 'Daytona getter replaces data', object: '{ daytona: {}, get daytona() { return new Daytona(); } }', review: true },
  { name: 'opaque getter replaces data', object: '{ daytona: {}, get daytona() { return opaque; } }', review: true },
  { name: 'external accessor pair replaces data', object: '{ daytona: new Daytona(), get daytona() { return axios.create(); }, set daytona(v) {} }', review: false },
  { name: 'Daytona accessor pair replaces data', object: '{ daytona: {}, get daytona() { return new Daytona(); }, set daytona(v) {} }', review: true },
  { name: 'setter followed by external getter', object: '{ daytona: new Daytona(), set daytona(v) {}, get daytona() { return axios.create(); } }', review: false },
  { name: 'external data replaces getter', object: '{ get daytona() { return new Daytona(); }, daytona: axios.create() }', review: false },
] as const;

export const accessorMatrixCases = [false, true].flatMap(typed => accessorReviewCases.map(testCase => ({
  name: `${testCase.name} (typed=${typed})`, typed, path: 'use.js',
  source: `${sdk}const deps = ${testCase.object};\ndeps.daytona.get();\n`,
  outcome: testCase.review ? { kind: 'fixme' } as const : { kind: 'unchanged' } as const,
})).filter(testCase => typed || testCase.outcome.kind === 'fixme'));

export const accessorPairMatrixCases = ['get client() { return new Daytona(); }, set client(v) {}',
  'set client(v) {}, get client() { return new Daytona(); }'].map(members => ({
  name: `typed accessor pair ${members}`, typed: true,
  source: `${sdk}const deps = { ${members} };\ndeps.client.create();\n`,
  outcome: { kind: 'rewrite', expected: `${sdk}const deps = { ${members} };\ndeps.client.v0.create();\n` } as const,
}));
