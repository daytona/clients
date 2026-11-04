/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const programs = [
  "const Custom = class extends Daytona { constructor() { super({apiUrl: 'https://self.hosted'}); } }; new Custom();",
  "const Custom = class extends Daytona {}; new Custom({apiUrl: 'https://self.hosted'});",
  "const Custom = class Named extends Daytona {}; new Custom({apiUrl: 'https://self.hosted'});",
  "const Base = class extends Daytona {}; class Custom extends Base {} new Custom({apiUrl: 'https://self.hosted'});",
] as const;

it.each(programs.flatMap(code => [false, true].map(types => ({ code, types }))))(
  'refuses custom class-expression configs when types=$types: $code', async ({ code, types }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix + code });
    if (types) installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', '--drop-api-url']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  },
);

it.each([false, true])('refuses an aliased re-export after an unsuccessful sibling star lookup when types=%s', async types => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'inner.ts': "export { Daytona as Base } from '@daytonaio/sdk';",
    'barrel.ts': "export * from './inner.js'; export { Base as Client } from './inner.js';",
    'index.ts': "import { Client } from './barrel.js'; new Client({apiUrl: 'https://self.hosted'});" });
  if (types) installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});
