/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { loadMapping } from '../src/mapping.js';
import { allowedNewerMessage, newerPatchMessage, newerRefusal, olderMessage, unknownMessage } from '../src/v0-version.js';

const pinned = loadMapping().v0Version;
const [major, minor, patch] = pinned.split('.').map(Number);
const newerPatch = `${major}.${minor}.${(patch ?? 0) + 1}`;
const code = "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); d.create();\n";
const manifest = (range: string): string => JSON.stringify({ dependencies: { '@daytonaio/sdk': range } });
const packageLock = (version: string): string => JSON.stringify({ lockfileVersion: 3, packages: {
  '': { dependencies: { '@daytonaio/sdk': version } },
  'node_modules/@daytonaio/sdk': { version },
  'node_modules/other/node_modules/@daytona/sdk': { version: '2.0.0-alpha.7' },
  // Another package's own copy is not the project's install.
  'node_modules/other/node_modules/@daytonaio/sdk': { version: '0.999.0' },
} });

type Report = { readonly warnings?: readonly { readonly file: string; readonly message: string }[]; readonly refused?: { readonly reason: string; readonly message: string }; readonly items: readonly { readonly kind: string }[] };

async function run(files: Readonly<Record<string, string>>, flags: readonly string[] = [], at = '.'): Promise<{ readonly code: number; readonly report: Report; readonly root: string }> {
  const root = temporaryProject(files);
  const result = await invoke(join(root, at), ['--json', ...flags]);
  return { code: result.code, report: JSON.parse(result.output) as Report, root };
}

it('stays quiet when the installed 0.x version is the pinned release', async () => {
  // Given / When
  const { code: exit, report } = await run({ 'package.json': manifest(`^${pinned}`), 'package-lock.json': packageLock(pinned), 'index.ts': code });
  // Then
  expect(exit).toBe(0);
  expect(report.warnings).toBeUndefined();
});

it('warns, without a manual item, when the lockfile installs an older 0.x version', async () => {
  // Given: the manifest range admits the pin, but the lockfile installs 0.100.0; SDK types resolve.
  const root = temporaryProject({ 'package.json': manifest('^0.100.0'), 'package-lock.json': packageLock('0.100.0'), 'index.ts': code });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then: the lockfile is the evidence, and --strict does not count the warning.
  const report = JSON.parse(result.output) as Report;
  expect(report.warnings).toEqual([{ file: 'package-lock.json', message: olderMessage('0.100.0', pinned) }]);
  expect(report.items).toEqual([]);
  expect(result.code).toBe(0);
});

it('falls back to the manifest range when no lockfile exists', async () => {
  // Given / When
  const { report } = await run({ 'package.json': manifest('~0.150.2'), 'index.ts': code });
  // Then
  expect(report.warnings).toEqual([{ file: 'package.json', message: olderMessage('0.150.2', pinned) }]);
});

it('warns on a newer patch of the pinned minor', async () => {
  // Given: a Yarn classic lockfile.
  const yarn = `# yarn lockfile v1\n\n"@daytonaio/sdk@^${pinned}":\n  version "${newerPatch}"\n  resolved "https://registry.yarnpkg.com/x.tgz"\n`;
  // When
  const { code: exit, report } = await run({ 'package.json': manifest(`^${pinned}`), 'yarn.lock': yarn, 'index.ts': code });
  // Then
  expect(exit).toBe(0);
  expect(report.warnings).toEqual([{ file: 'yarn.lock', message: newerPatchMessage(newerPatch, pinned) }]);
});

it('refuses a newer 0.x minor and writes nothing', async () => {
  // Given: pnpm installs a 0.x minor the bundled .v0 predates.
  const pnpm = `lockfileVersion: '9.0'\n\npackages:\n\n  '@daytonaio/sdk@0.999.0':\n    resolution: {integrity: sha512-x}\n`;
  const files = { 'package.json': manifest('^0.999.0'), 'pnpm-lock.yaml': pnpm, 'index.ts': code };
  // When
  const { code: exit, report, root } = await run(files);
  // Then
  expect(exit).toBe(2);
  expect(report.refused).toEqual({ reason: 'newer-v0', message: newerRefusal('0.999.0', 'pnpm-lock.yaml', pinned) });
  expect(readFileSync(join(root, 'package.json'), 'utf8')).toBe(files['package.json']);
  expect(readFileSync(join(root, 'index.ts'), 'utf8')).toBe(code);
});

it('migrates a newer 0.x minor with --allow-newer-v0 and warns', async () => {
  // Given: Bun's text lockfile.
  const bun = `{\n  "lockfileVersion": 1,\n  "packages": {\n    "@daytonaio/sdk": ["@daytonaio/sdk@0.999.0", "", {}, "sha512-x"],\n  },\n}\n`;
  // When
  const { code: exit, report, root } = await run({ 'package.json': manifest('^0.999.0'), 'bun.lock': bun, 'index.ts': code }, ['--allow-newer-v0']);
  // Then
  expect(exit).toBe(0);
  expect(report.warnings).toEqual([{ file: 'bun.lock', message: allowedNewerMessage('0.999.0', pinned) }]);
  expect(readFileSync(join(root, 'index.ts'), 'utf8')).toContain('d.v0.create()');
});

it('reads the workspace lockfile above the scanned package and a Berry lockfile', async () => {
  // Given: only the workspace root has a lockfile.
  const berry = `__metadata:\n  version: 8\n\n"@daytonaio/sdk@npm:^0.100.0":\n  version: 0.100.0\n  resolution: "@daytonaio/sdk@npm:0.100.0"\n`;
  // When
  const { report } = await run({ '.git/HEAD': '', 'yarn.lock': berry, 'packages/app/package.json': manifest('^0.100.0'), 'packages/app/index.ts': code }, [], 'packages/app');
  // Then
  expect(report.warnings).toEqual([{ file: '../../yarn.lock', message: olderMessage('0.100.0', pinned) }]);
});

it('warns when no version can be determined', async () => {
  // Given / When
  const { report } = await run({ 'package.json': manifest('*'), 'index.ts': code });
  // Then
  expect(report.warnings).toEqual([{ file: 'package.json', message: unknownMessage(pinned) }]);
});

it('ignores 2.x versions and peer ranges', async () => {
  // Given: a peer range below the pin, and a lockfile that already resolves 2.x.
  const files = { 'package.json': JSON.stringify({ peerDependencies: { '@daytonaio/sdk': '>=0.100.0' }, dependencies: { '@daytona/sdk': '^2.0.0-alpha.7' } }),
    'package-lock.json': JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/@daytona/sdk': { version: '2.0.0-alpha.7' } } }), 'index.ts': code };
  // When
  const { report } = await run(files);
  // Then
  expect(report.warnings).toBeUndefined();
});

it('ignores the version outside upgrade mode', async () => {
  // Given / When
  const { code: exit, report } = await run({ 'package.json': manifest('0.999.0'), 'index.ts': code }, ['--suggest-v2']);
  // Then
  expect(exit).toBe(0);
  expect(report.refused).toBeUndefined();
  expect(report.warnings).toBeUndefined();
});
