/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const prelude = "import { Daytona } from '@daytonaio/sdk';\n";
const logicalWrites = ['||=', '??=', '&&='] as const;

it.each(logicalWrites.flatMap(operator => [false, true].map(drop => ({ operator, drop }))))(
  'refuses custom URL logical writes with $operator and drop=$drop', async ({ operator, drop }) => {
    // Given
    const code = `const config = {}; config.apiUrl ${operator} 'https://custom.example'; new Daytona(config);\n`;
    const root = temporaryProject({ 'index.ts': prelude + code, 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
    expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]) });
  },
);

it.each(['apiUrl', 'serverUrl'].flatMap(key => [false, true].map(drop => ({ key, drop }))))(
  'refuses imported config use-site custom $key writes with drop=$drop', async ({ key, drop }) => {
    // Given
    const code = `import { config as options } from './config.js'; options.${key} = 'https://custom.example'; new Daytona(options);\n`;
    const root = temporaryProject({ 'index.ts': prelude + code, 'config.ts': 'export const config = {};\n' });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
    expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url', file: 'index.ts' })]) });
  },
);

it.each(['apiUrl', 'serverUrl'].flatMap(key => ['=', ...logicalWrites].map(operator => ({ key, operator }))))(
  'removes literal element cloud $key assignments with $operator', async ({ key, operator }) => {
    // Given
    const assignment = `config['${key}'] ${operator} 'https://app.daytona.io/api/';`;
    const code = `const config = {}; ${assignment} keep(); new Daytona(config);\n`;
    const root = temporaryProject({ 'index.ts': prelude + code });
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(contents(root)['index.ts']).toBe(prelude.replace('@daytonaio/sdk', '@daytona/sdk') + code.replace(assignment, ''));
  },
);

it('keeps imported use-site cloud assignments while preserving shadowed objects', async () => {
  // Given
  const assignment = "options['apiUrl'] = 'https://app.daytona.io/api';";
  const code = `import { config as options } from './config.js'; ${assignment}\nfunction other() { const options = {}; options.apiUrl = 'https://custom.example'; }\nnew Daytona(options);\n`;
  const root = temporaryProject({ 'index.ts': prelude + code, 'config.ts': 'export const config = {};\n' });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  const output = contents(root)['index.ts'] ?? '';
  expect(output).toContain('FIXME(daytona-upgrade):');
  expect(output.replace(/^.*FIXME\(daytona-upgrade\):.*\n/gm, '')).toBe(prelude.replace('@daytonaio/sdk', '@daytona/sdk') + code);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});

it.each([
  "config[key] = 'https://app.daytona.io/api';",
  "use(config['apiUrl'] = 'https://app.daytona.io/api');",
  "config['apiUrl'] += 'https://app.daytona.io/api';",
])('refuses unverifiable element assignment without override: %s', async assignment => {
  // Given
  const root = temporaryProject({ 'index.ts': prelude + `const config = {}; ${assignment} new Daytona(config);\n` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each([
  "config[key] = 'https://app.daytona.io/api';",
  "use(config['apiUrl'] = 'https://app.daytona.io/api');",
  "config['apiUrl'] += 'https://app.daytona.io/api';",
])('preserves unverifiable element assignment with override: %s', async assignment => {
  // Given
  const code = `const config = {}; ${assignment} new Daytona(config);\n`;
  const root = temporaryProject({ 'index.ts': prelude + code });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(prelude.replace('@daytonaio/sdk', '@daytona/sdk') + code);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'config-field' })]) });
});
