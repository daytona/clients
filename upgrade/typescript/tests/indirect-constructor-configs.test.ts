/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const forms = [
  'const Bound = Daytona.bind(null, CONFIG); new Bound();',
  'Reflect.construct(Daytona, [CONFIG]);',
  'Reflect.construct(Daytona, [...[CONFIG]]);',
  'const args = [CONFIG] as const; Reflect.construct(Daytona, [...args]);',
] as const;

it.each(forms.flatMap(form => ['apiUrl', 'serverUrl'].flatMap(field => [false, true].map(drop => ({ form, field, drop }))))) (
  'refuses custom $field when $form, drop=$drop', async ({ form, field, drop }) => {
    // Given
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
      'app.ts': "import { Daytona } from '@daytonaio/sdk';\n" + form.replace('CONFIG', `{ ${field}: 'https://self.hosted/api' }`),
    });
    installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code, result.output).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  },
);

it.each(forms.flatMap(form => ['{}', 'undefined', 'void 0', "{ apiKey: 'key' }", "{ apiUrl: 'https://app.daytona.io/api' }", "{ serverUrl: 'https://app.daytona.io/api' }"].map(config => ({ form, config })))) (
  'accepts Cloud or absent URLs when $form uses $config', async ({ form, config }) => {
    // Given
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
      'app.ts': "import { Daytona } from '@daytonaio/sdk';\n" + form.replace('CONFIG', config),
    });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code, result.output).toBe(0);
    expect(contents(root)['app.ts']).not.toMatch(/(?:apiUrl|serverUrl)/);
    expect(contents(root)['package.json']).toContain('@daytona/sdk');
  },
);

it.each([
  'Daytona.bind(null);',
  'Reflect.construct(Daytona, []);',
  'Reflect.construct(Daytona, [...[], {}]);',
  'class Other {} Other.bind(null, { apiUrl: "https://self.hosted" });',
  'class Other {} Reflect.construct(Other, [{ apiUrl: "https://self.hosted" }]);',
  'function run(Reflect: { construct(...args: unknown[]): unknown }) { Reflect.construct(Daytona, [{ apiUrl: "https://self.hosted" }]); }',
])('ignores absent or unrelated constructor configs when %s', async program => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'app.ts': "import { Daytona } from '@daytonaio/sdk';\n" + program,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toContain(program);
});
