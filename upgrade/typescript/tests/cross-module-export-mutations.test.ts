/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  { state: 'export const client = new Daytona();', write: "import * as m from './state.js'; m.client = other;", call: 'm.client.create()' },
  { state: 'exports.client = new Daytona();', write: "const m = require('./state'); m.client = other;", call: 'm.client.create()' },
  { state: 'module.exports.client = new Daytona();', write: "const m = require('./state'); m.client = other;", call: 'm.client.create()' },
])('kills the client proof when another module overwrites $state', async ({ state, write, call }) => {
  // Given
  const root = temporaryProject({
    'jsconfig.json': '{"compilerOptions":{"allowJs":true,"moduleResolution":"node"},"include":["*.js"]}',
    'state.js': `import { Daytona } from '@daytonaio/sdk'; ${state}\n`,
    'index.js': `const other = { create() { return 'other'; } }; ${write} ${call};\n`,
  });
  installFakeSdk(root);
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.js']).toContain(`${call};`);
});

it.each(['same file', 'third module'])('kills an exported object property proof when overwritten in %s', async reader => {
  // Given
  const mutation = "import { state } from './state.js'; state.client = { create() { return 'other'; } };";
  const root = temporaryProject({
    'package.json': '{"type":"module"}',
    'jsconfig.json': '{"compilerOptions":{"allowJs":true,"moduleResolution":"node"},"include":["*.js"]}',
    'state.js': "import { Daytona } from '@daytonaio/sdk'; export const state = { client: new Daytona() };",
    'mutate.js': mutation,
    'index.js': reader === 'same file' ? `${mutation} state.client.create();` : "import './mutate.js'; import { state } from './state.js'; state.client.create();",
  });
  installFakeSdk(root);
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.js']).toContain('state.client.create();');
});
