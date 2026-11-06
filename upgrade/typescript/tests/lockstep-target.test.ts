/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';
import { loadMapping } from '../src/mapping.js';
import { companionCommand, targetRange, withTarget } from '../src/sdk-target.js';

it.each([
  ['2.0.0-alpha.7', '2.0.0-alpha.7'],
  ['2.0.0-rc.1', '2.0.0-rc.1'],
  ['2.3.1', '^2.3.1'],
  ['3.0.0', '^3.0.0'],
  ['0.0.0', '^2.0.0'],
  ['0.1.0', '^2.0.0'],
])('targets the SDK release a %s tool was published with', (toolVersion, range) => {
  // Given: the mapping's development range.
  // When / Then: a published tool targets its own SDK release; a 0.x build keeps the fallback.
  expect(targetRange(toolVersion, '^2.0.0')).toBe(range);
});

it('writes the exact pre-release the tool was published with', async () => {
  // Given: a 0.x project and the mapping a 2.0.0-alpha.7 tool loads.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); d.create();\n" });
  const mapping = withTarget(loadMapping(), '2.0.0-alpha.7');
  // When
  const result = await invoke(root, ['--json'], mapping);
  // Then: the manifest pins that alpha, the one a ^2 range could never install.
  expect(result.code).toBe(0);
  expect(JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))).toEqual({ dependencies: { '@daytona/sdk': '2.0.0-alpha.7' } });
  expect(JSON.parse(result.output).dependencies).toEqual([{ file: 'package.json', from: '@daytonaio/sdk@0.224.0', to: '@daytona/sdk@2.0.0-alpha.7' }]);
});

it('accepts an ancestor that already declares the exact target pre-release', async () => {
  // Given: the workspace root was migrated by the same tool release.
  const root = temporaryProject({ '.git/HEAD': '', 'package.json': '{"dependencies":{"@daytona/sdk":"2.0.0-alpha.7"}}',
    'app/index.ts': "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.create();\n" });
  // When
  const result = await invoke(join(root, 'app'), ['--json'], withTarget(loadMapping(), '2.0.0-alpha.7'));
  // Then
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'manual-dependency')).toEqual([]);
});

it('a re-run of a pre-release tool reports no dependency item', async () => {
  // Given: a project this 2.0.0-alpha.7 tool already migrated.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); d.create();\n" });
  const mapping = withTarget(loadMapping(), '2.0.0-alpha.7');
  await invoke(root, ['--json'], mapping);
  // When
  const result = await invoke(root, ['--json'], mapping);
  // Then: the pinned pre-release is the target, not a manual dependency.
  const report = JSON.parse(result.output) as { readonly items: readonly { readonly kind: string }[]; readonly changedFiles: readonly string[] };
  expect(report.items.filter(item => item.kind === 'manual-dependency')).toEqual([]);
  expect(report.changedFiles).toEqual([]);
});

it.each(['2.0.0-alpha.9'])('an older tool keeps a newer pre-release %s', async range => {
  // Given: a 2.0.0-alpha.8 tool on a project already past it.
  const manifest = JSON.stringify({ dependencies: { '@daytona/sdk': range } });
  const root = temporaryProject({ 'package.json': manifest,
    'index.ts': "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.v0.create();\n" });
  // When
  const result = await invoke(root, ['--json', '--strict'], withTarget(loadMapping(), '2.0.0-alpha.8'));
  // Then: no downgrade, no manual item.
  expect(readFileSync(join(root, 'package.json'), 'utf8')).toBe(manifest);
  expect((JSON.parse(result.output) as { readonly items: readonly { readonly kind: string }[] }).items
    .filter(item => item.kind === 'manual-dependency')).toEqual([]);
});

it('a pre-release tool replaces a pre-lockstep ^2.0.0 range', async () => {
  // Given: ^2.0.0 (what the pre-lockstep tool wrote) installs no 2.x alpha.
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'index.ts': "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.v0.create();\n" });
  // When
  await invoke(root, ['--json'], withTarget(loadMapping(), '2.0.0-alpha.8'));
  // Then
  expect(JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))).toEqual({ dependencies: { '@daytona/sdk': '2.0.0-alpha.8' } });
});

const client = "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.v0.create();\n";
const migrate = async (range: string, tool: string): Promise<{ readonly written: unknown; readonly items: readonly { readonly kind: string; readonly message: string }[] }> => {
  const root = temporaryProject({ 'package.json': JSON.stringify({ dependencies: { '@daytona/sdk': range } }), 'index.ts': client });
  const result = await invoke(root, ['--json'], withTarget(loadMapping(), tool));
  expect((JSON.parse(result.output) as { readonly refused?: unknown }).refused).toBeUndefined();
  return { written: (JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as { readonly dependencies: Record<string, string> }).dependencies['@daytona/sdk'],
    items: (JSON.parse(result.output) as { readonly items: readonly { readonly kind: string; readonly message: string }[] }).items.filter(item => item.kind === 'manual-dependency') };
};

it.each([
  ['2.0.0-alpha.7', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  ['2.0.0-alpha.8', '2.0.0', '^2.0.0'],
  ['^2.0.0-alpha.9', '2.0.0', '^2.0.0'],
  ['^2.3.1', '2.5.0', '^2.5.0'],
  ['^2.5.0', '2.3.1', '^2.5.0'],
  ['^2.5.0', '2.0.0-alpha.3', '^2.5.0'],
  ['2.1.0', '2.0.0-alpha.3', '2.1.0'],
  ['^2.0.0', '2.0.0-alpha.3', '2.0.0-alpha.3'],
  // An exact 2.0.0 pin, in any spelling, is a project already on the stable release: kept.
  ['2.0.0', '2.0.0-alpha.3', '2.0.0'],
  ['=2.0.0', '2.0.0-alpha.3', '=2.0.0'],
  ['v2.0.0', '2.0.0-alpha.3', 'v2.0.0'],
  // A pre-release range also admits the stale 2.0.0-rc.1: pinned, never lowered.
  ['^2.0.0-alpha.8', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  ['>=2.0.0-alpha.8', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  ['^2.0.0-alpha.9', '2.0.0-alpha.8', '2.0.0-alpha.9'],
  ['^2.0.0-alpha.7', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  // An exclusive bound at or above the target is kept, never rewritten to a version it excludes.
  ['>2.0.0-alpha.8', '2.0.0-alpha.8', '>2.0.0-alpha.8'],
  ['>2.0.0-alpha.9', '2.0.0-alpha.8', '>2.0.0-alpha.9'],
  // A union is at the target only when every alternative is.
  ['^2.0.0-alpha.3 || >2.0.0-alpha.8', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  // A union floored on an inclusive alternative keeps that floor.
  ['>=2.0.0-alpha.9 || >3.0.0', '2.0.0-alpha.8', '2.0.0-alpha.9'],
  ['>2.0.0-alpha.9 || >=2.0.0-alpha.9', '2.0.0-alpha.8', '2.0.0-alpha.9'],
  // The published 2.0.0-rc.1 is a stale pre-alpha build without .v0: not ahead of an alpha.
  ['2.0.0-rc.1', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  ['^2.0.0-rc.1', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  // Any other beta or rc is ahead of an alpha tool: kept, or pinned to its floor.
  ['2.0.0-beta.1', '2.0.0-alpha.8', '2.0.0-beta.1'],
  ['^2.0.0-beta.1', '2.0.0-alpha.8', '2.0.0-beta.1'],
  ['2.0.0-rc.2', '2.0.0-alpha.8', '2.0.0-rc.2'],
])('moves %s under a %s tool to %s', async (range, tool, expected) => {
  // Given / When
  const { written, items } = await migrate(range, tool);
  // Then: a lower release of the target's major moves to the target; a higher one stays.
  expect(written).toBe(expected);
  expect(items).toEqual([]);
});

it.each(['3.0.0', '^3.1.0', '>=3'])('keeps a later major %s and reports it', async range => {
  // Given / When
  const { written, items } = await migrate(range, '2.0.0-alpha.8');
  // Then: never downgraded across a major.
  expect(written).toBe(range);
  expect(items).toEqual([expect.objectContaining({
    message: `@daytona/sdk@${range} is newer than this daytona-upgrade release targets (2.0.0-alpha.8); use the daytona-upgrade release of that SDK version.` })]);
});

it.each([['^2.0.0', true], ['2.0.0-alpha.8', false]])('ancestor %s under an alpha tool: manual item %s', async (range, reported) => {
  // Given: the workspace root declares the SDK; app/ is scanned.
  // app/index.ts still calls the 0.x root, so the run rewrites it.
  const root = temporaryProject({ '.git/HEAD': '', 'package.json': JSON.stringify({ dependencies: { '@daytona/sdk': range } }),
    'app/index.ts': client.replace('d.v0.create()', 'd.create()') });
  // When
  const result = await invoke(join(root, 'app'), ['--json'], withTarget(loadMapping(), '2.0.0-alpha.8'));
  // Then: only the target or a later release of its major certifies.
  const items = (JSON.parse(result.output) as { readonly items: readonly { readonly kind: string; readonly message: string }[] }).items
    .filter(item => item.kind === 'manual-dependency');
  expect(items).toEqual(reported ? [expect.objectContaining({ file: '../package.json',
    message: 'no Daytona dependency was updated; change the dependency in this file to "@daytona/sdk": "2.0.0-alpha.8"' })] : []);
});

it.each([['0.0.0', 'uvx daytona-upgrade'], ['2.0.0-alpha.8', 'uvx daytona-upgrade@2.0.0-alpha.8'], ['2.3.1', 'uvx daytona-upgrade@2.3.1']])(
  'names the Python tool of the %s release', (toolVersion, command) => {
    // Given / When / Then: the companion tool of the same release, not the registry's latest.
    expect(companionCommand(toolVersion)).toBe(command);
  });

it('reports an old package name declared on a later major instead of renaming it', async () => {
  // Given / When
  const root = temporaryProject({ 'package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': '3.0.0' } }), 'index.ts': client });
  const result = await invoke(root, ['--json'], withTarget(loadMapping(), '2.0.0-alpha.8'));
  // Then: never renamed and downgraded across a major.
  expect(JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))).toEqual({ dependencies: { '@daytonaio/sdk': '3.0.0' } });
  expect((JSON.parse(result.output) as { readonly items: readonly { readonly kind: string; readonly message: string }[] }).items).toContainEqual(expect.objectContaining({
    kind: 'manual-dependency', message: '@daytonaio/sdk@3.0.0 is newer than this daytona-upgrade release targets (2.0.0-alpha.8); use the daytona-upgrade release of that SDK version.' }));
});

it.each([
  ['3.0.0', '@daytona/sdk@3.0.0 is newer than this daytona-upgrade release targets (2.0.0-alpha.8); use the daytona-upgrade release of that SDK version.'],
  ['^2.0.0-alpha.9', 'no Daytona dependency was updated; change the dependency in this file to "@daytona/sdk": "2.0.0-alpha.9"'],
])('ancestor guidance for %s never asks for a downgrade', async (range, message) => {
  // Given: the workspace root declares the SDK above the target; app/ is scanned.
  const root = temporaryProject({ '.git/HEAD': '', 'package.json': JSON.stringify({ dependencies: { '@daytona/sdk': range } }),
    'app/index.ts': client.replace('d.v0.create()', 'd.create()') });
  // When
  const result = await invoke(join(root, 'app'), ['--json'], withTarget(loadMapping(), '2.0.0-alpha.8'));
  // Then: the same decision as a rewrite in the project root.
  expect((JSON.parse(result.output) as { readonly items: readonly { readonly kind: string; readonly message: string }[] }).items
    .filter(item => item.kind === 'manual-dependency').map(item => item.message)).toEqual([message]);
});

it('reports a later-major peer range as a peer dependency', async () => {
  // Given / When
  const root = temporaryProject({ 'package.json': JSON.stringify({ peerDependencies: { '@daytonaio/sdk': '3.0.0' }, dependencies: { '@daytonaio/sdk': '0.100.0' } }), 'index.ts': client });
  const result = await invoke(root, ['--json'], withTarget(loadMapping(), '2.0.0-alpha.8'));
  // Then: the dedicated peer report, not the later-major one.
  const items = (JSON.parse(result.output) as { readonly items: readonly { readonly kind: string; readonly message: string }[] }).items;
  expect(items).toContainEqual(expect.objectContaining({ kind: 'peer-dependency' }));
  expect(items.some(item => item.message.includes('is newer than this daytona-upgrade release targets'))).toBe(false);
});

it('a stamped release writes the SDK release of its own version', () => {
  // Given: the built tool under a package.json stamped as the publish job does.
  const packageRoot = fileURLToPath(new URL('..', import.meta.url));
  const stamped = mkdtempSync(join(tmpdir(), 'daytona-upgrade-stamped-'));
  try {
    cpSync(join(packageRoot, 'dist'), join(stamped, 'dist'), { recursive: true });
    symlinkSync(join(packageRoot, 'node_modules'), join(stamped, 'node_modules'), 'junction');
    const manifest = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')) as Record<string, unknown>;
    writeFileSync(join(stamped, 'package.json'), JSON.stringify({ ...manifest, version: '2.0.0-alpha.42' }));
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.230.0"}}', 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" });
    // When
    const result = spawnSync(process.execPath, [join(stamped, 'dist/cli.js'), root, '--json'], { encoding: 'utf8' });
    // Then: the version reaches the written range (no placeholder ^2.0.0).
    expect(result.status, result.stderr).toBe(0);
    expect(JSON.parse(result.stdout).dependencies).toEqual([{ file: 'package.json', from: '@daytonaio/sdk@^0.230.0', to: '@daytona/sdk@2.0.0-alpha.42' }]);
  } finally {
    rmSync(stamped, { recursive: true, force: true });
  }
});
