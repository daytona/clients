/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.120.0"}}';
const ambiguous = [
  'var {env} = process; if (false) { var {env} = {env: {}} } env.DAYTONA_API_URL = VALUE;',
  'function configure() { env.DAYTONA_API_URL = VALUE; } var {env} = process; configure();',
  'var {env} = {env: {}}; function configure() { env.DAYTONA_API_URL = VALUE; } var {env} = process; configure();',
  'var p = process; if (false) { var p = {env: {}}; } p.env.DAYTONA_API_URL = VALUE;',
  'var {env} = process; var {env} = {env: {}}; env.DAYTONA_API_URL = VALUE;',
  'var p = process; var p = {env: {}}; p.env.DAYTONA_API_URL = VALUE;',
  'var env = process.env; var env = {}; env.DAYTONA_API_URL = VALUE;',
] as const;

it.each(ambiguous)('refuses literal custom URLs when lexical declaration order cannot prove provenance: %s', async program => {
  // Given
  const code = program.replace('VALUE', "'https://self/api'");
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': code });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url' }), expect.objectContaining({ kind: 'custom-api-url' }),
  ]));
  expect(contents(root)).toEqual(before);
});

it.each(ambiguous)('reports dynamic URLs when lexical declaration order cannot prove provenance: %s', async program => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': program.replace('VALUE', 'endpoint') });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual([expect.objectContaining({ kind: 'env-api-url' })]);
});

it.each([
  "const m = {DAYTONA_API_URL: 'https://self/api'}; delete m.DAYTONA_API_URL; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; delete m['DAYTONA_API_URL']; Object.assign(process.env, m);",
  "const m = {}; m.DAYTONA_API_URL = 'https://self/api'; delete m.DAYTONA_API_URL; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; const alias = m; delete alias.DAYTONA_API_URL; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; const alias = m; delete m.DAYTONA_API_URL; Object.assign(process.env, alias);",
])('ignores removed mapping entries when a straight-line delete precedes the copy: %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it.each([
  "const m = {DAYTONA_API_URL: 'https://self/api'}; Object.assign(process.env, m); delete m.DAYTONA_API_URL;",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; if (flag) delete m.DAYTONA_API_URL; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; delete m.DAYTONA_API_URL; mutate(m); Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; delete m.DAYTONA_API_URL; m.DAYTONA_API_URL = 'https://later/api'; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; delete m[key]; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; function apply() { delete m.DAYTONA_API_URL; } Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}, hardened = Object.defineProperty(m, 'DAYTONA_API_URL', {configurable: false}); delete m.DAYTONA_API_URL; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; for (;;) { delete m.DAYTONA_API_URL; Object.assign(process.env, m); }",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; let alias = m; alias = other; delete alias.DAYTONA_API_URL; Object.assign(process.env, m);",
  "const m = {DAYTONA_API_URL: 'https://self/api'}; delete m.OTHER; Object.assign(process.env, m);",
])('retains custom mapping evidence when deletion does not prove absence at the copy: %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': code });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]));
});
