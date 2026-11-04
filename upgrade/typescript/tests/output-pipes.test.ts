/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const cli = fileURLToPath(new URL('../dist/cli.js', import.meta.url));

it.skipIf(process.platform === 'win32').each(['--print', '--dry'])('exits cleanly when the %s reader closes early', flag => {
  // Given a preview larger than the pipe buffer, consumed by a one-byte reader.
  const root = temporaryProject({ 'package.json': JSON.stringify({
    dependencies: { '@daytonaio/sdk': '0.224.0' }, description: 'x'.repeat(512_000),
  }) });
  // When
  const result = spawnSync('bash', ['-o', 'pipefail', '-c',
    '"$1" "$2" "$3" --force --yes "$4" | head -c 1 >/dev/null',
    'pipe-test', process.execPath, cli, root, flag], { encoding: 'utf8' });
  // Then
  expect(result.status, result.stderr).toBe(0);
  expect(result.stderr).toBe('');
});

it('separates printed files and the report when files have no final newline', async () => {
  // Given
  const root = temporaryProject({
    'first.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().list();",
    'second.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().list();",
  });
  // When
  const result = await invoke(root, ['--print']);
  // Then
  expect(result.output).toContain('.v0.list();\n--- second.ts\n');
  expect(result.output).toContain('.v0.list();\nWould change');
});

it('emits a project-relative dry patch that git apply accepts', async () => {
  // Given
  const root = temporaryProject({ 'nested/app.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().list();\n" });
  const preview = await invoke(root, ['--dry']);
  const patch = preview.output.slice(0, preview.output.indexOf('Would change'));
  // When
  const applied = spawnSync('git', ['apply', '-'], { cwd: root, input: patch, encoding: 'utf8' });
  // Then
  expect(applied.status, applied.stderr).toBe(0);
  expect(patch).toContain('--- a/nested/app.ts\n+++ b/nested/app.ts\n');
  expect(patch).not.toContain(root);
  expect(contents(root)['nested/app.ts']).toContain('new Daytona().v0.list()');
});
