/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const prefix = "import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';\n";
const programs = [
  'class Sub extends Daytona { constructor(config: DaytonaConfig) { super(config); } } new Sub({apiUrl: ENDPOINT}).create();',
  'class Sub extends Daytona { constructor(private readonly config: DaytonaConfig) { super(config); } } new Sub({apiUrl: ENDPOINT}).create();',
  'class Sub extends Daytona { constructor(key: string, config: DaytonaConfig) { super(config); } } new Sub("k", {apiUrl: ENDPOINT}).create();',
  'class Sub extends Daytona { constructor(url: string) { super({apiUrl: url}); } } new Sub(ENDPOINT).create();',
  'class Sub extends Daytona { constructor({apiUrl: url}: DaytonaConfig) { super({apiUrl: url}); } } new Sub({apiUrl: ENDPOINT}).create();',
  'class Sub extends Daytona { constructor({apiUrl}: DaytonaConfig) { super({apiUrl}); } } new Sub({apiUrl: ENDPOINT}).create();',
  'class Sub extends Daytona { constructor({config}: {config: DaytonaConfig}) { super(config); } } new Sub({config: {apiUrl: ENDPOINT}}).create();',
  'class Sub extends Daytona { constructor([url]: [string]) { super({apiUrl: url}); } } new Sub([ENDPOINT]).create();',
  'class Base extends Daytona { constructor(config: DaytonaConfig) { super(config); } } class Sub extends Base {} new Sub({apiUrl: ENDPOINT}).create();',
  'class Base extends Daytona { constructor(url: string) { super({apiUrl: url}); } } class Sub extends Base { constructor(options: {endpoint: string}) { super(options.endpoint); } } new Sub({endpoint: ENDPOINT}).create();',
  'const Sub = class extends Daytona { constructor(url: string) { const endpoint = url; super({apiUrl: endpoint}); } }; new Sub(ENDPOINT).create();',
] as const;

it.each(programs.flatMap(code => [false, true].flatMap(types => [false, true].map(drop => ({ code, types, drop })))))(
  'hard-refuses custom constructor inputs when types=$types, drop=$drop: $code', async ({ code, types, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest,
      'index.ts': prefix + code.replace('ENDPOINT', '"https://self.hosted"') });
    if (types) installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  },
);

it.each(programs)('accepts Cloud constructor inputs with the explicit assertion: %s', async code => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'index.ts': prefix + code.replace('ENDPOINT', '"https://app.daytona.io/api"') });
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('.v0.create()');
});

it.each([programs[3], programs[4], programs[5], programs[7], programs[10]])(
  'drops proven Cloud URL parameters without an assertion: %s', async code => {
    // Given
    const root = temporaryProject({ 'package.json': manifest,
      'index.ts': prefix + code.replace('ENDPOINT', '"https://app.daytona.io/api"') });
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(contents(root)['index.ts']).toContain('super({})');
    expect(contents(root)['index.ts']).toContain('.v0.create()');
  },
);

it.each(['new Sub(endpoint);', 'new Sub(...args);'])(
  'keeps unknown inputs unresolved alongside a Cloud call: %s', async unknown => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix +
      'class Sub extends Daytona { constructor(url: string) { super({apiUrl: url}); } } new Sub("https://app.daytona.io/api"); ' + unknown });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it.each([false, true])('hard-refuses a constructor input across project modules when types=%s', async types => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'base.ts': prefix + 'export class Sub extends Daytona { constructor(url: string) { super({apiUrl: url}); } }',
    'index.ts': 'import { Sub } from "./base.js"; new Sub("https://self.hosted").create();' });
  if (types) installFakeSdk(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('keeps custom URL-looking constructor arguments when they only supply the API key', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix +
    'class Sub extends Daytona { constructor(key: string) { super({apiKey: key}); } } new Sub("https://self.hosted").create();' });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain('new Sub("https://self.hosted").v0.create()');
});

it('hard-refuses when any call site supplies a custom URL among Cloud inputs', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix +
    'class Sub extends Daytona { constructor(url: string) { super({apiUrl: url}); } } new Sub("https://app.daytona.io/api"); new Sub("https://self.hosted");' });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
