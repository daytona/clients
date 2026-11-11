/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const aliases = [
  'let alias = config;',
  'var alias = config;',
  'const alias = config;',
  'let first = config; const alias = first;',
  'const first = config; let alias = first;',
] as const;
const prefix = "import { Daytona } from '@daytonaio/sdk';\n" +
  "import { mutate } from './mutate.js';\n" +
  "const config = { apiKey: 'key' };\n";
const mutator = "export function mutate(c) { c.apiUrl = 'https://self.example'; }\n";
const unresolved = 'cannot verify the API URL of this DaytonaConfig; if it targets Daytona Cloud, rerun with --drop-api-url';
const asserted = '--drop-api-url asserts Cloud usage; review this unchanged call manually.';

it.each(aliases)('refuses migration when a config alias escapes: %s', async alias => {
  // Given a Cloud config whose alias is passed to a mutator before construction.
  const root = temporaryProject({ 'index.ts': prefix + `${alias} mutate(alias); new Daytona(config);`, 'mutate.ts': mutator });
  const before = contents(root);
  // When upgrading without asserting the unknown URL is safe.
  const result = await invoke(root, ['--json']);
  // Then the escape refuses atomically through the existing unverified-config path.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
  expect(JSON.parse(result.output)).toMatchObject({
    refused: { message: 'Cannot verify that every DaytonaConfig targets Daytona Cloud. If they do, rerun with --drop-api-url; for a custom API URL, stay on SDK 0.x. No files were written.' },
    items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url', message: unresolved })]),
  });
});

it.each(aliases)('reports manual review when an escaped config alias is asserted safe: %s', async alias => {
  // Given the same escape with an explicit operator assertion.
  const root = temporaryProject({ 'index.ts': prefix + `${alias} mutate(alias); new Daytona(config);`, 'mutate.ts': mutator });
  // When allowing unverifiable URLs.
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then the escape remains visible as a manual item instead of silently verified.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'config-field', message: asserted }));
});

it.each(['let', 'var', 'const'].flatMap(kind => [false, true].map(drop => ({ kind, drop }))))(
  'refuses a known custom URL through a local $kind alias with drop=$drop', async ({ kind, drop }) => {
    // Given a locally visible mutator with a known custom URL.
    const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
      "const config = { apiKey: 'key' };\n" + mutator + `${kind} alias = config; mutate(alias); new Daytona(config);` });
    const before = contents(root);
    // When checking the config, even with permission to drop unknown URLs.
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then known custom endpoints remain an unconditional refusal.
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  });

it.each(['let', 'var', 'const'])('keeps a config verified when a %s alias only exposes a scalar', async kind => {
  // Given a mutable alias used only for a credential value read.
  const root = temporaryProject({ 'index.ts': prefix + `${kind} alias = config; consume(alias.apiKey); new Daytona(config);` });
  // When checking config escapes.
  const result = await invoke(root, ['--json']);
  // Then reading a field is not confused with passing the config object.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'config-field' }));
});
