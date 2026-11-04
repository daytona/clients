/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const cases: readonly { readonly name: string; readonly source: string; readonly suffix?: string; readonly expected?: 'custom' | 'cloud' | 'none' | 'unresolved' }[] = JSON.parse(
  readFileSync(new URL('../../mapping/yaml-environment-cases.json', import.meta.url), 'utf8'),
);
const endpoints = ['https://self.hosted', 'https://app.daytona.io/api'] as const;

it.each(cases.flatMap(test => endpoints.map(endpoint => ({ ...test, endpoint }))))(
  'migrates only Cloud projects when YAML uses $name: $endpoint', async ({ source, endpoint, suffix = '', expected }) => {
    // Given
    const yaml = source.replace('ENDPOINT', endpoint + suffix);
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'index.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n", 'compose.yaml': yaml });
    const before = contents(root);
    const outcome = expected ?? (endpoint === 'https://app.daytona.io/api' && suffix === '' ? 'cloud' : 'custom');
    const reported = expected !== 'none';
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(!reported || outcome !== 'custom' ? 0 : 2);
    expect(JSON.parse(result.output).items.map((item: { readonly kind: string }) => item.kind)
      .filter((kind: string) => kind === 'env-api-url' || kind === 'custom-api-url'))
      .toEqual(!reported ? [] : outcome === 'custom' ? ['env-api-url', 'custom-api-url'] : ['env-api-url']);
    expect(contents(root)['compose.yaml']).toBe(yaml);
    if (outcome !== 'custom' || !reported) expect(contents(root)['index.ts']).toContain('new Daytona().v0.create()');
    else expect(contents(root)).toEqual(before);
  },
);
