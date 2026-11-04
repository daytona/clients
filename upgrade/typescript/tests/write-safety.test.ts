/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { main } from '../src/cli.js';
import { contents, fixture, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

afterEach(() => { vi.unstubAllEnvs(); });

it.each([
  { manager: 'npm', lockfile: 'package-lock.json' }, { manager: 'yarn', lockfile: 'yarn.lock' },
  { manager: 'pnpm', lockfile: 'pnpm-lock.yaml' }, { manager: 'bun', lockfile: 'bun.lock' },
])('only prints the install command when the detected manager is $manager', async ({ manager, lockfile }) => {
  // Given: a local executable records invocation without installing or using the network.
  const root = temporaryProject();
  const marker = join(root, 'installer-ran');
  const stub = `#!/usr/bin/env node\nrequire('node:fs').writeFileSync(${JSON.stringify(marker)}, 'executed'); process.exit(97);\n`;
  const project = temporaryProject({
    'package.json': '{"dependencies":{"@daytona/sdk":"0.224.0"},"scripts":{"postinstall":"exit 98"}}',
    [lockfile]: '{}', [`bin/${manager}`]: stub,
  });
  chmodSync(join(project, 'bin', manager), 0o755);
  vi.stubEnv('PATH', `${join(project, 'bin')}:${process.env['PATH'] ?? ''}`);
  const output: string[] = [];
  // When
  const code = await main([project, '--force', '--yes'], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(0);
  expect(existsSync(marker)).toBe(false);
  expect(output.join('').split('\n')).toContain(`  Run ${manager} install in ${relative(process.cwd(), project).replaceAll('\\', '/')}`);
});

it('prints without refusing or writing when the Git tree is dirty', async () => {
  // Given
  const root = fixture('named-imports');
  expect(spawnSync('git', ['init', '-q'], { cwd: root, env: { ...process.env, LC_ALL: 'C', LANG: 'C' } }).status).toBe(0);
  const before = contents(root);
  const output: string[] = [];
  // When
  const code = await main([root, '--print', '--yes'], { out: text => output.push(text), err: text => output.push(text) });
  // Then
  expect(code).toBe(0);
  expect(output.join('')).toContain('daytona.v0.create()');
  expect(contents(root)).toEqual(before);
});

it.skipIf(process.getuid?.() === 0).each([
  { flags: [], source: "import { Daytona } from '@daytona/sdk';\nnew Daytona().create();\n" },
  { flags: ['--suggest-v2'], source: "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\nawait d.v0.list();\n" },
  { flags: ['--remove-suggestions'], source: "import { Daytona } from '@daytona/sdk';\n// daytona-v2: obsolete\nconst d = new Daytona();\n" },
])('preserves original bytes when a target directory is read-only in mode $flags', async ({ flags, source }) => {
  // Given: atomic replacement needs a writable parent, not a writable target.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytona/sdk":"0.224.0"}}\n', 'a.ts': '\uFEFF' + source, 'locked/b.ts': source });
  const originals = ['package.json', 'a.ts', 'locked/b.ts'].map(file => ({ file: join(root, file), bytes: readFileSync(join(root, file)) }));
  chmodSync(join(root, 'locked'), 0o555);
  try {
    // When
    const result = await invoke(root, flags, suggestionMapping);
    // Then
    expect(result.code).toBe(1);
    expect(result.errors).toContain('b.ts');
    for (const original of originals) expect(readFileSync(original.file)).toEqual(original.bytes);
  } finally { chmodSync(join(root, 'locked'), 0o755); }
});
