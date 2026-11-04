/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each(['any', 'unknown', 'missing', 'uninstalled'])(
  'leaves a bare external wrapper clean when its return type is %s', async returnType => {
    // Given external wrapper receivers, with installed 0.x SDK types unless dependencies are absent.
    const installed = returnType !== 'uninstalled';
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'index.ts': "import { Daytona } from '@daytonaio/sdk'; import { makeDaytona } from '@mycorp/infra'; const daytona = makeDaytona(); daytona.create();",
      ...(installed ? {
        'node_modules/@mycorp/infra/package.json': '{"name":"@mycorp/infra","types":"index.d.ts"}',
        'node_modules/@mycorp/infra/index.d.ts': `export declare function makeDaytona()${returnType === 'missing' ? '' : `: ${returnType}`};`,
      } : {}),
    });
    if (installed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then
    expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
    expect(contents(root)['index.ts']).toContain('daytona.create();');
    expect(contents(root)['index.ts']).not.toContain('daytona.v0.create();');
    expect(result.code).toBe(0);
  },
);

it.each([
  "import { makeClient } from '@mycorp/infra'; const daytona = makeClient(); daytona.create();",
  "import { makeDaytona as makeClient } from '@mycorp/infra'; const client = makeClient(); client.create();",
  "import { makeClient } from '@mycorp/Daytona-infra'; const client = makeClient(); client.create();",
  "import { makeClient } from '@mycorp/infra'; const client = makeClient(); const DAYTONA = client; DAYTONA.create();",
  "import * as infra from '@mycorp/infra'; const client = infra.makeDaytona(); client.create();",
])('leaves external origins clean even when their provenance mentions Daytona: %s', async declaration => {
  // Given no node_modules, but a conclusive bare external origin despite Daytona names.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': `import { Daytona } from '@daytonaio/sdk'; ${declaration}`,
  });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['index.ts']).toContain(declaration);
  expect(result.code).toBe(0);
});

it.each(['any', 'unknown'])(
  'leaves a neutrally named external client clean when its installed return type is %s', async returnType => {
    // Given installed declarations with unknown types but a conclusive external origin.
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'index.ts': "import { Daytona } from '@daytonaio/sdk'; import { makeClient } from '@vendor/cache'; const cacheClient = makeClient(); cacheClient.get('key');",
      'node_modules/@vendor/cache/package.json': '{"name":"@vendor/cache","types":"index.d.ts"}',
      'node_modules/@vendor/cache/index.d.ts': `export declare function makeClient(): ${returnType};`,
    });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then
    expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toEqual([]);
    expect(contents(root)['index.ts']).toContain("cacheClient.get('key');");
    expect(contents(root)['index.ts']).not.toContain('cacheClient.v0.');
    expect(result.code).toBe(0);
  },
);

it('ignores a Daytona-named receiver when an installed wrapper returns a concrete unrelated type', async () => {
  // Given a misleading name, but concrete external non-Daytona return evidence.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytonaio/sdk'; import { makeDaytona } from '@mycorp/infra'; const daytona = makeDaytona(); daytona.create();",
    'node_modules/@mycorp/infra/package.json': '{"name":"@mycorp/infra","types":"index.d.ts"}',
    'node_modules/@mycorp/infra/index.d.ts': 'export declare function makeDaytona(): { create(): void };',
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(JSON.parse(result.output).items).toEqual([]);
  expect(result.code).toBe(0);
});
