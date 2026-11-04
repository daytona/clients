/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';

it.each(['apiUrl', 'serverUrl'].flatMap(field => ['||=', '??=', '&&=']
  .flatMap(operator => [false, true].map(drop => ({ field, operator, drop })))))(
  'refuses unbound skipped custom $field $operator when drop=$drop', async ({ field, operator, drop }) => {
    // Given
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'app.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();",
      'config.min.js': `/* Daytona config */ config.${field} ${operator} 'https://internal.example/api';`,
    });
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ file: 'config.min.js',
      kind: 'custom-api-url',
      message: 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.' }));
    expect(readFileSync(join(root, 'config.min.js'), 'utf8')).toBe(`/* Daytona config */ config.${field} ${operator} 'https://internal.example/api';`);
  });

it.each(['import', 'export'])('copies trailing block directives to both split %s declarations', async keyword => {
  // Given
  const suppression = '/* eslint-disable-line no-alert */';
  const root = temporaryProject({ 'app.ts': `${keyword} { Daytona, Sandbox } from '@daytonaio/sdk'; ${suppression}\n` });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  const lines = readFileSync(join(root, 'app.ts'), 'utf8').trimEnd().split('\n');
  expect(lines).toHaveLength(2);
  expect(lines.every(line => line.endsWith(suppression))).toBe(true);
});
