/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterAll, beforeAll, expect, it } from 'vitest';
import { contents, here, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const sdk = process.env['DAYTONA_UPGRADE_TEST_SDK'] ? resolve(process.env['DAYTONA_UPGRADE_TEST_SDK']) : '';
const bundled = sdk !== '' && existsSync(join(sdk, 'dist/cjs/v0/index.js'));
const fixture = bundled ? mkdtempSync(join(tmpdir(), 'daytona-typed-mocks-')) : '';
const dependencies = join(fixture, 'node_modules');
const message = 'Mock replacement reaches a Daytona client consumer but its v0 shape is unresolved; give the replacement a v0 attribute before using SDK 2.x.';

beforeAll(() => {
  if (!bundled) return;
  copyFileSync(join(here, 'mock-dependencies/package.json'), join(fixture, 'package.json'));
  const install = spawnSync('npm', ['install', '--ignore-scripts', '--no-package-lock', '--no-audit', '--no-fund'], { cwd: fixture, encoding: 'utf8' });
  expect(install.status, install.stdout + install.stderr).toBe(0);
}, 30000);
afterAll(() => { if (bundled) rmSync(fixture, { recursive: true, force: true }); });

it.skipIf(!bundled).each(['jest-mock-extended', 'vitest-mock-extended'])(
  'compiles and executes migrated deep mocks when using real SDK 2.x and %s', async module => {
    // Given: real mock libraries with configured returns and call assertions.
    const vitest = module === 'vitest-mock-extended';
    const source = `import { it, expect } from '${vitest ? 'vitest' : '@jest/globals'}';
import type { Daytona, Sandbox } from '@daytonaio/sdk';
import { mock, mockDeep as makeMock } from '${module}';
import * as mocks from '${module}';
function build(client: Daytona) { return client.create(); }
it('returns the configured sandbox and records the call', async () => {
  const sandbox = mock<Sandbox>();
  const fake = makeMock<Daytona>();
  fake.create.mockResolvedValue(sandbox);
  expect(await build(fake)).toBe(sandbox);
  expect(fake.create).toHaveBeenCalledTimes(1);
});
it('routes namespace-imported deep mocks too', async () => {
  const sandbox = mock<Sandbox>();
  const fake = mocks.mockDeep<Daytona>();
  fake.create.mockResolvedValue(sandbox);
  expect(await build(fake)).toBe(sandbox);
  expect(fake.create).toHaveBeenCalledTimes(1);
});
`;
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'index.test.ts': source,
      'tsconfig.json': JSON.stringify({ compilerOptions: { target: 'ES2022', module: 'commonjs', moduleResolution: 'node',
        strict: true, skipLibCheck: true, esModuleInterop: true, lib: ['es2022', 'dom', 'esnext.disposable'], outDir: 'dist' }, include: ['index.test.ts'] }),
      'jest.config.cjs': "module.exports = { testMatch: ['**/dist/*.test.js'], transform: {} };",
    });
    installFakeSdk(root);
    for (const name of [module, 'vitest', '@jest']) symlinkSync(join(dependencies, name), join(root, 'node_modules', name), 'dir');
    mkdirSync(join(root, 'node_modules/@daytona'), { recursive: true });
    symlinkSync(sdk, join(root, 'node_modules/@daytona/sdk'), 'dir');
    const compile = (): void => {
      const result = spawnSync(process.execPath, [join(dependencies, 'typescript/bin/tsc')], { cwd: root, encoding: 'utf8' });
      expect(result.status, result.stdout + result.stderr).toBe(0);
    };
    const execute = (): void => {
      const args = vitest ? [join(dependencies, 'vitest/vitest.mjs'), 'run', 'index.test.ts', '--maxWorkers=1']
        : [join(dependencies, 'jest/bin/jest.js'), '--runInBand'];
      const result = spawnSync(process.execPath, args, { cwd: root, encoding: 'utf8' });
      expect(result.status, result.stdout + result.stderr).toBe(0);
    };
    compile();
    execute();
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then: compilation uses the real root's v0 getter type, not fake declarations.
    expect(result.code, result.output).toBe(0);
    expect(JSON.parse(result.output).items).toEqual([]);
    compile();
    execute();
    // Mutation proves the real-surface assertion observes the migrated setup.
    writeFileSync(join(root, 'index.test.ts'), (contents(root)['index.test.ts'] ?? '').replaceAll('return client.v0.create();', 'return undefined;'));
    const mutant = vitest ? [join(dependencies, 'vitest/vitest.mjs'), 'run', 'index.test.ts', '--maxWorkers=1']
      : [join(dependencies, 'jest/bin/jest.js'), '--runInBand'];
    if (!vitest) compile();
    expect(spawnSync(process.execPath, mutant, { cwd: root }).status).toBe(1);
  },
);

it.each(['jest-mock-extended', 'vitest-mock-extended'].flatMap(module => [
  { module, factory: 'mock<Daytona>()' },
  { module, factory: 'mockDeep<Daytona>({ create: () => Promise.resolve(sandbox) })' },
  { module, factory: 'mockDeep<Other>()' },
]))('leaves unsafe typed factories for strict review when using $factory from $module', async ({ module, factory }) => {
  // Given
  const source = `import type { Daytona } from '@daytonaio/sdk';
import { mock, mockDeep } from '${module}';
function build(client: Daytona) { return client.create(); }
const fake = ${factory};
fake.create.mockResolvedValue(sandbox);
fake.snapshot.list.mockResolvedValue([]);
build(fake);
`;
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', 'index.ts': source });
  installFakeSdk(root);
  if (bundled) symlinkSync(join(dependencies, module), join(root, 'node_modules', module), 'dir');
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target', message }));
  expect(contents(root)['index.ts']).toContain(`const fake = ${factory};\nfake.create.mockResolvedValue(sandbox);\nfake.snapshot.list.mockResolvedValue([]);`);
});
