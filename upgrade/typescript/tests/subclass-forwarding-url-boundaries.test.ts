/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const prefix = "import { Daytona } from '@daytonaio/sdk';\n";

it.each([
  { 'barrel.ts': "import { Daytona as Base } from '@daytonaio/sdk'; export { Base as Client };",
    'index.ts': "import { Client } from './barrel.js'; new Client({apiUrl: 'https://self.hosted'});" },
  { 'inner.ts': "export { Daytona as Base } from '@daytonaio/sdk';", 'barrel.ts': "export { Base as Client } from './inner.js';",
    'index.ts': "import { Client } from './barrel.js'; new Client({apiUrl: 'https://self.hosted'});" },
  { 'barrel.ts': "export * from '@daytonaio/sdk';",
    'index.ts': "import { Daytona as Client } from './barrel.js'; new Client({apiUrl: 'https://self.hosted'});" },
  { 'barrel.ts': "export { Daytona } from '@daytonaio/sdk';",
    'index.ts': "import * as sdk from './barrel.js'; new sdk.Daytona({apiUrl: 'https://self.hosted'});" },
])('refuses custom URLs through statically connected local barrels: %j', async files => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, ...files });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});

it.each([false, true])('refuses unverifiable subclass forwarding when SDK types resolve=%s', async types => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix +
    'class Custom extends Daytona { constructor(options) { mutate(options); super(options); } } new Custom({apiKey: "key"});' });
  if (types) installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});

it.each([false, true])('accepts a literal Cloud config in super when SDK types resolve=%s', async types => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix +
    'class Custom extends Daytona { constructor() { super({apiUrl: "https://app.daytona.io/api", apiKey: "key"}); } } new Custom();' });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).not.toContain('apiUrl');
  expect(contents(root)['index.ts']).toContain('apiKey: "key"');
});

it('refuses a proven custom constructor default even when unverifiable configs are accepted', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix +
    'class Custom extends Daytona { constructor(options = {apiUrl: "https://self.hosted"}) { super(options); } } new Custom();' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});
