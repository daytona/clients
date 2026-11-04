/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const programs = [
  prefix + "class Custom extends Daytona { constructor() { super({apiUrl: 'https://self.hosted'}); } } new Custom();",
  prefix + "class Custom extends Daytona {} new Custom({apiUrl: 'https://self.hosted'});",
  prefix + "class Base extends Daytona {} class Custom extends Base {} new Custom({apiUrl: 'https://self.hosted'});",
  "import { Client } from './barrel.js'; new Client({apiUrl: 'https://self.hosted'});",
] as const;

it.each(programs.flatMap(code => [false, true].map(types => ({ code, types }))))('refuses custom subclass or re-export config when types=$types: $code', async ({ code, types }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': code,
    'barrel.ts': "export { Daytona as Client } from '@daytonaio/sdk';" });
  if (types) installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});

it.each(['{create(): void}', 'string', 'number', '{create(): void} | {create(): string}'])('does not add manual client warnings when the receiver has proven unrelated type %s', async type => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'index.ts': prefix + `function local(client: ${type}) { client.create(); }\n` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]));
  expect(contents(root)['index.ts']).not.toContain('FIXME(daytona-upgrade)');
});
