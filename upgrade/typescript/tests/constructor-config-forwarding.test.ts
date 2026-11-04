/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const prefix = "import { Daytona, type DaytonaConfig } from '@daytonaio/sdk';\n";
const accepted = [
  'class Sub extends Daytona { constructor(private readonly size: number) { super({apiKey: process.env.DAYTONA_API_KEY}); } } new Sub(4).create();',
  'class Sub extends Daytona { constructor(key: string) { super({apiKey: key}); } } new Sub("k").create();',
  'class Sub extends Daytona { constructor(key: string) { super({apiKey: key, apiUrl: "https://app.daytona.io/api"}); } } new Sub("k").create();',
  'class Sub extends Daytona { constructor(options: {size: number}) { super({apiKey: "k"}); } } new Sub({size: 4}).create();',
  'class Base extends Daytona { constructor(key: string) { super({apiKey: key}); } } class Sub extends Base {} new Sub("k").create();',
  'class Base extends Daytona {} class Sub extends Base { constructor(key: string) { super({apiKey: key}); } } new Sub("k").create();',
  'class Base extends Daytona { constructor(size: number) { super({apiKey: "k"}); } } class Sub extends Base { constructor(key: string) { super(4); } } new Sub("k").create();',
  'const Sub = class extends Daytona { constructor(key: string) { super({apiKey: key}); } }; new Sub("k").create();',
  'class Sub extends Daytona { constructor(private readonly size: number) { super({apiKey: "k"}); } } const pool = new Sub(4); pool.create();',
  'class Sub extends Daytona { constructor(private readonly size: number) { super({apiKey: "k"}); } warm() { this.create(); } } new Sub(4).warm();',
] as const;
const refused = [
  'class Sub extends Daytona { constructor(cfg: DaytonaConfig) { super(cfg); } } new Sub({apiUrl: "https://self.hosted"}).create();',
  'class Sub extends Daytona { constructor(url: string) { super({apiUrl: url}); } } new Sub("https://self.hosted").create();',
  'class Sub extends Daytona { constructor(cfg: DaytonaConfig) { const options = cfg; super(options); } } new Sub({}).create();',
  'class Sub extends Daytona { constructor(url: string) { super({apiUrl: url.trim()}); } } new Sub("https://self.hosted").create();',
  'class Sub extends Daytona { constructor(cfg: DaytonaConfig) { super({...cfg}); } } new Sub({}).create();',
  'class Base extends Daytona { constructor(url: string) { super({apiUrl: url}); } } class Sub extends Base {} new Sub("https://self.hosted").create();',
  'class Base extends Daytona {} class Sub extends Base {} new Sub({apiUrl: "https://self.hosted"}).create();',
] as const;

it.each(accepted.flatMap(code => [false, true].map(types => ({ code, types }))))(
  'migrates non-config constructor arguments when types=$types: $code', async ({ code, types }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix + code });
    if (types) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(contents(root)['index.ts']).toContain('.v0.create()');
    expect(contents(root)['index.ts']).not.toContain('apiUrl');
    expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: 'custom-api-url' }),
    ]));
  },
);

it.each(refused.flatMap(code => [false, true].map(types => ({ code, types }))))(
  'refuses unverifiable config forwarding when types=$types: $code', async ({ code, types }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'index.ts': prefix + code });
    if (types) installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  },
);
