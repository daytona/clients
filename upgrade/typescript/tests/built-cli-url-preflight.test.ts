/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { copyFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { beforeAll, expect, it } from 'vitest';
import { contents, temporaryProject } from './helpers.js';

const cli = fileURLToPath(new URL('../dist/cli.js', import.meta.url));
beforeAll(() => {
  const compiler = fileURLToPath(new URL('../node_modules/typescript/bin/tsc', import.meta.url));
  const built = spawnSync(process.execPath, [compiler], { cwd: fileURLToPath(new URL('..', import.meta.url)), encoding: 'utf8' });
  expect(built.status, built.stdout + built.stderr).toBe(0);
  copyFileSync(new URL('../src/mapping.json', import.meta.url), new URL('../dist/mapping.json', import.meta.url));
});

it.each([
  { code: 'const cfg = {}; console.log(cfg); function run() { new Daytona(cfg).create(); }', flag: false, status: 0 },
  { code: 'const cfg = {}; configure({ cfg }); new Daytona(cfg).create();', flag: false, status: 2 },
  { code: 'const cfg = {}; configure({ cfg }); new Daytona(cfg).create();', flag: true, status: 0 },
  { code: "const cfg = {}; cfg.apiUrl = 'https://custom.example'; new Daytona(cfg).create();", flag: true, status: 2 },
])('enforces URL preflight through the built CLI: $code, flag=$flag', ({ code, flag, status }) => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" + code,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
  const before = contents(root);
  // When
  const result = spawnSync(process.execPath, [cli, root, '--force', '--yes', '--json', ...(flag ? ['--drop-api-url'] : [])], { encoding: 'utf8' });
  // Then
  expect(result.status, result.stderr).toBe(status);
  if (status === 2) {
    expect(contents(root)).toEqual(before);
    expect(JSON.parse(result.stdout).changedFiles).toEqual([]);
  } else expect(contents(root)['index.ts']).toContain('new Daytona(cfg).v0.create()');
});
