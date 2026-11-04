/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each(['apiUrl', 'serverUrl'].flatMap(key => ['https://api.stripe.com/v1', '/api/v1', 'http://localhost:3000']
  .flatMap(url => [false, true].map(drop => ({ key, url, drop })))))('refuses unrelated generated custom $key=$url when drop=$drop', async ({ key, url, drop }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'bundle.min.js': `var vendor={${key}:${JSON.stringify(url)}}; var DaytonaSdk={};` });
  // When
  const result = await invoke(root, ['--dry', '--json', ...(drop ? ['--drop-api-url'] : [])]);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
});

it.each(['apiKey', 'organizationId', 'target', 'constructor', 'annotation', 'satisfies', 'assertion'])(
  'refuses generated custom literals regardless of config shape: %s', async shape => {
    // Given
    const config = '{apiUrl:"https://custom.test"}';
    const input = shape === 'constructor' ? `new Daytona(${config});` : shape === 'annotation' ? `const config: DaytonaConfig = ${config};` :
      shape === 'satisfies' ? `const config = ${config} satisfies DaytonaConfig;` : shape === 'assertion' ? `const config = ${config} as DaytonaConfig;` :
        `/* Daytona */ var config = {apiUrl:"https://custom.test", ${shape}:"value"};`;
    const root = temporaryProject({ 'package.json': manifest, 'bundle.min.ts': input });
    // When
    const result = await invoke(root, ['--dry', '--json', '--drop-api-url']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
  });

it.each([
  "globalThis.process.env.DAYTONA_API_URL = 'https://custom.test'",
  "global.process.env['DAYTONA_API_URL'] = 'https://custom.test'",
  "globalThis['process'].env.DAYTONA_API_URL = 'https://custom.test'",
  "const { env } = require('node:process'); env.DAYTONA_API_URL = 'https://custom.test'",
  "const { env: settings } = require('process'); settings.DAYTONA_SERVER_URL = 'https://custom.test'",
  "import { env } from 'node:process'; env.DAYTONA_API_URL = 'https://custom.test'",
].flatMap(input => ['ts', 'js', 'mjs', 'cjs'].map(extension => ({ input, extension }))))('refuses custom environment writes in $extension when bootstrap uses $input', async ({ input, extension }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, [`bootstrap.${extension}`]: input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  expect(report).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
});

it.each([
  "const globalThis = { process: { env: {} } }; globalThis.process.env.DAYTONA_API_URL = 'https://custom.test'",
  "function require(name) { return { env: {} }; } const { env } = require('node:process'); env.DAYTONA_API_URL = 'https://custom.test'",
])('ignores unrelated environment lookalikes when names are shadowed: %s', async input => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'bootstrap.js': input });
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'env-api-url' }));
});
