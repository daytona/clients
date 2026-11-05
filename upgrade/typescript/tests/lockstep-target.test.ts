/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';
import { loadMapping } from '../src/mapping.js';
import { targetRange, withTarget } from '../src/sdk-target.js';

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

it.each(['2.0.0-alpha.9', '^2.0.0-alpha.9'])('an older tool keeps a newer pre-release %s', async range => {
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
  return { written: (JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as { readonly dependencies: Record<string, string> }).dependencies['@daytona/sdk'],
    items: (JSON.parse(result.output) as { readonly items: readonly { readonly kind: string; readonly message: string }[] }).items.filter(item => item.kind === 'manual-dependency') };
};

it.each([
  ['2.0.0-alpha.7', '2.0.0-alpha.8', '2.0.0-alpha.8'],
  ['2.0.0-alpha.8', '2.0.0', '^2.0.0'],
  ['^2.0.0-alpha.9', '2.0.0', '^2.0.0'],
  ['^2.3.1', '2.5.0', '^2.5.0'],
  ['^2.5.0', '2.3.1', '^2.5.0'],
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

it.each([['^2.0.0', true], ['2.0.0-alpha.8', false], ['3.0.0', true]])('ancestor %s under an alpha tool: manual item %s', async (range, reported) => {
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
