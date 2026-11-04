/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

const sdk = "import { Daytona } from '@daytona/sdk';\n";

export const accessorWriteCases = [
  { name: 'direct assignment', write: 'deps.client = other;' },
  { name: 'element assignment', write: "deps['client'] = other;" },
  { name: 'compound assignment', write: 'deps.client ||= other;' },
  { name: 'arithmetic assignment', write: 'deps.client += other;' },
  { name: 'Object.assign', write: 'Object.assign(deps, { client: other });' },
  { name: 'spread merge', write: 'Object.assign(deps, { ...replacement });' },
  { name: 'target alias', write: 'const alias = deps; Object.assign(alias, { client: other });' },
  { name: 'member alias', write: 'const alias = deps; alias.client = other;' },
  { name: 'nested write', write: 'function replace() { deps.client = other; }' },
  { name: 'unwritten pair', write: '' },
  { name: 'unrelated property write', write: 'deps.other = other;' },
] as const;

export const accessorWriteMatrixCases = [false, true].flatMap(typed =>
  [false, true].flatMap(setterFirst => accessorWriteCases
    // Without SDK types, whole-object assign already suppresses getter evidence.
    .filter(({ name }) => typed || !['Object.assign', 'spread merge'].includes(name))
    .map(({ name, write }) => {
    const getter = 'get client() { return new Daytona(); }';
    const setter = 'set client(v) {}';
    const members = setterFirst ? `${setter}, ${getter}` : `${getter}, ${setter}`;
    const source = `${sdk}const other = {}; const replacement = { client: other };\nconst deps = { ${members}, other: null };\n${write}\ndeps.client.create();\n`;
    const written = write !== '' && name !== 'unrelated property write';
    return {
      name: `accessor ${name} (typed=${typed}, setterFirst=${setterFirst})`, typed, path: 'use.js', source,
      outcome: written || !typed ? { kind: 'fixme' } as const :
        { kind: 'rewrite', expected: source.replace('deps.client.create()', 'deps.client.v0.create()') } as const,
    };
  }))).concat([false, true].map(written => {
    const source = `${sdk}let stored = new Daytona();\nconst deps = { get client() { return stored; }, set client(v) { stored = v; } };\n${written ? 'deps.client = { create() {} };\n' : ''}deps.client.create();\n`;
    return {
      name: `accessor replaces backing value (written=${written})`, typed: true, path: 'use.js', source,
      outcome: written ? { kind: 'fixme' } as const :
        { kind: 'rewrite', expected: source.replace('deps.client.create()', 'deps.client.v0.create()') } as const,
    };
  }));
