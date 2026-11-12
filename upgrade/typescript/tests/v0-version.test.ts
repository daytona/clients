/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { chmodSync, readFileSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { here, installFakeSdk, invoke, temporaryProject } from './helpers.js';
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
  const pnpm = `lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    dependencies:\n      '@daytonaio/sdk':\n        specifier: ^0.999.0\n        version: 0.999.0\n\npackages:\n\n  '@daytonaio/sdk@0.999.0':\n    resolution: {integrity: sha512-x}\n`;
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
  const berry2 = `${berry}\n"root@workspace:.":\n  version: 0.0.0-use.local\n\n"app@workspace:packages/app":\n  version: 0.0.0-use.local\n`;
  const { report } = await run({ '.git/HEAD': '', 'yarn.lock': berry2, 'package.json': JSON.stringify({ name: 'root', private: true, workspaces: ['packages/*'] }),
    'packages/app/package.json': manifest('^0.100.0'), 'packages/app/index.ts': code }, [], 'packages/app');
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

// Lockfiles also list copies other packages pull in, and other workspaces'
// copies: only the declaring manifest's own resolution counts.
const transitive = '0.999.0';
it.each([
  ['yarn.lock', `# yarn lockfile v1\n\n"@daytonaio/sdk@^${pinned}":\n  version "${pinned}"\n\n"@daytonaio/sdk@^${transitive}":\n  version "${transitive}"\n`],
  ['yarn.lock', `__metadata:\n  version: 8\n\n"@daytonaio/sdk@npm:^${pinned}":\n  version: ${pinned}\n\n"@daytonaio/sdk@npm:^${transitive}":\n  version: ${transitive}\n`],
  ['pnpm-lock.yaml', `lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      '@daytonaio/sdk':\n        specifier: ^${pinned}\n        version: ${pinned}\npackages:\n  '@daytonaio/sdk@${pinned}':\n    resolution: {integrity: a}\n  '@daytonaio/sdk@${transitive}':\n    resolution: {integrity: b}\n`],
  ['bun.lock', `{\n  "lockfileVersion": 1,\n  "packages": {\n    "@daytonaio/sdk": ["@daytonaio/sdk@${pinned}", "", {}, "a"],\n    "other/@daytonaio/sdk": ["@daytonaio/sdk@${transitive}", "", {}, "b"],\n  },\n}\n`],
  ['package-lock.json', packageLock(pinned)],
])('ignores a transitive newer copy in %s', async (lockfile, text) => {
  // Given: the project resolves the pinned release; another package pulls in a newer copy.
  // When
  const { code: exit, report } = await run({ 'package.json': manifest(`^${pinned}`), [lockfile]: text, 'index.ts': code });
  // Then
  expect(report.refused).toBeUndefined();
  expect(report.warnings).toBeUndefined();
  expect(exit).toBe(0);
});

it.each([
  ['package-lock.json', JSON.stringify({ lockfileVersion: 3, packages: {
    'packages/a': { name: 'a' }, 'packages/b': { name: 'b' }, 'node_modules/@daytonaio/sdk': { version: pinned }, 'packages/b/node_modules/@daytonaio/sdk': { version: transitive } } })],
  ['pnpm-lock.yaml', `lockfileVersion: '9.0'\nimporters:\n  packages/a:\n    dependencies:\n      '@daytonaio/sdk':\n        specifier: ^${pinned}\n        version: ${pinned}\n  packages/b:\n    dependencies:\n      '@daytonaio/sdk':\n        specifier: ^${transitive}\n        version: ${transitive}\n`],
  ['bun.lock', `{\n  "lockfileVersion": 1,\n  "workspaces": {\n    "": {\n      "name": "root",\n    },\n    "packages/a": {\n      "name": "a",\n    },\n    "packages/b": {\n      "name": "b",\n    },\n  },\n  "packages": {\n    "@daytonaio/sdk": ["@daytonaio/sdk@${pinned}", "", {}, "a"],\n    "b/@daytonaio/sdk": ["@daytonaio/sdk@${transitive}", "", {}, "b"],\n  },\n}\n`],
])('migrates one workspace without reading a sibling workspace\'s copy in %s', async (lockfile, text) => {
  // Given: packages/b is on a newer minor; packages/a, the scanned package, is on the pin.
  const files = { '.git/HEAD': '', [lockfile]: text,
    'packages/a/package.json': JSON.stringify({ name: 'a', dependencies: { '@daytonaio/sdk': `^${pinned}` } }), 'packages/a/index.ts': code,
    'packages/b/package.json': JSON.stringify({ name: 'b', dependencies: { '@daytonaio/sdk': `^${transitive}` } }) };
  // When
  const { report } = await run(files, [], 'packages/a');
  // Then
  expect(report.refused).toBeUndefined();
  expect(report.warnings).toBeUndefined();
});

it('reads a workspace\'s own nested copy before the hoisted one', async () => {
  // Given: the root hoists the pin; packages/a keeps its own newer copy.
  const lock = JSON.stringify({ lockfileVersion: 3, packages: { 'packages/a': { name: 'a' },
    'node_modules/@daytonaio/sdk': { version: pinned }, 'packages/a/node_modules/@daytonaio/sdk': { version: transitive } } });
  // When
  const { code: exit, report } = await run({ '.git/HEAD': '', 'package-lock.json': lock,
    'packages/a/package.json': manifest(`^${transitive}`), 'packages/a/index.ts': code }, [], 'packages/a');
  // Then
  expect(exit).toBe(2);
  expect(report.refused?.message).toBe(newerRefusal(transitive, '../../package-lock.json', pinned));
});

it('falls back to the manifest when the lockfile does not resolve the dependency', async () => {
  // Given: a lockfile from before the Daytona dependency was added.
  const { code: exit, report } = await run({ 'package.json': manifest(`^${transitive}`),
    'package-lock.json': JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/left-pad': { version: '1.0.0' } } }), 'index.ts': code });
  // Then
  expect(exit).toBe(2);
  expect(report.refused?.message).toBe(newerRefusal(transitive, 'package.json', pinned));
});

it.each([
  ['npm-shrinkwrap.json', JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/@daytonaio/sdk': { version: '0.100.0' } } })],
  ['package-lock.json', JSON.stringify({ lockfileVersion: 1, dependencies: { '@daytonaio/sdk': { version: '0.100.0' } } })],
  ['pnpm-lock.yaml', `lockfileVersion: 5.4\nspecifiers:\n  '@daytonaio/sdk': ^0.100.0\ndependencies:\n  '@daytonaio/sdk': 0.100.0\npackages:\n  /@daytonaio/sdk/0.100.0:\n    resolution: {integrity: a}\n`],
  ['pnpm-lock.yaml', `lockfileVersion: '6.0'\ndependencies:\n  '@daytonaio/sdk':\n    specifier: ^0.100.0\n    version: 0.100.0(typescript@5.0.0)\n`],
])('reads %s variants', async (lockfile, text) => {
  // Given / When
  const { report } = await run({ 'package.json': manifest('^0.100.0'), [lockfile]: text, 'index.ts': code });
  // Then
  expect(report.warnings).toEqual([{ file: lockfile, message: olderMessage('0.100.0', pinned) }]);
});

it('resolves an npm alias by its dependency key', async () => {
  // Given: `"sdk0": "npm:@daytonaio/sdk@^0.100.0"` installs under node_modules/sdk0.
  const files = { 'package.json': JSON.stringify({ dependencies: { sdk0: 'npm:@daytonaio/sdk@^0.100.0' } }),
    'package-lock.json': JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/sdk0': { name: '@daytonaio/sdk', version: '0.100.0' } } }),
    'index.ts': "import { Daytona } from 'sdk0'; new Daytona();\n" };
  // When
  const { report } = await run(files);
  // Then
  expect(report.warnings).toEqual([{ file: 'package-lock.json', message: olderMessage('0.100.0', pinned) }]);
});

it('follows a symlinked lockfile', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest('^0.100.0'), 'locks/package-lock.json': packageLock('0.100.0'), 'index.ts': code });
  symlinkSync(join(root, 'locks/package-lock.json'), join(root, 'package-lock.json'));
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect((JSON.parse(result.output) as Report).warnings).toEqual([{ file: 'package-lock.json', message: olderMessage('0.100.0', pinned) }]);
});

it('reads the ancestor manifest on a partial-root run', async () => {
  // Given: the repository root declares a newer minor; only src/ is scanned.
  const files = { '.git/HEAD': '', 'package.json': manifest('0.999.0'), 'src/index.ts': code };
  // When
  const { code: exit, report } = await run(files, [], 'src');
  // Then
  expect(exit).toBe(2);
  expect(report.refused?.message).toBe(newerRefusal('0.999.0', '../package.json', pinned));
});

it('warns unknown when nothing declares Daytona', async () => {
  // Given: no manifest anywhere up to the Git root.
  // When
  const { code: exit, report } = await run({ '.git/HEAD': '', 'index.ts': code });
  // Then
  expect(exit).toBe(0);
  expect(report.warnings).toEqual([{ file: '.', message: unknownMessage(pinned) }]);
});

it('reports unknown only for a manifest with no version evidence', async () => {
  // Given: one declaration names a version, an alias names none.
  const files = { 'package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': '^0.100.0', sdk0: 'npm:@daytonaio/sdk' } }), 'index.ts': code };
  // When
  const { report } = await run(files);
  // Then
  expect(report.warnings).toEqual([{ file: 'package.json', message: olderMessage('0.100.0', pinned) }]);
});

it('a migrated declaration ignores a stale lockfile', async () => {
  // Given: package.json is on 2.x; package-lock.json still resolves the old 0.x minor.
  const files = { 'package.json': JSON.stringify({ dependencies: { '@daytona/sdk': '^2.0.0' } }),
    'package-lock.json': JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/@daytona/sdk': { version: transitive } } }),
    'index.ts': "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.v0.create();\n" };
  // When
  const { code: exit, report } = await run(files);
  // Then
  expect(exit).toBe(0);
  expect(report.refused).toBeUndefined();
  expect(report.warnings).toBeUndefined();
});

it.each([false, true])('a root package-lock.json resolves only its own workspaces (workspace=%s)', async workspace => {
  // Given: the root lockfile hoists a newer minor; tools/x is on the pin.
  const lock = JSON.stringify({ lockfileVersion: 3, packages: {
    '': { workspaces: workspace ? ['tools/x'] : [] },
    ...(workspace ? { 'tools/x': { name: 'x' } } : {}),
    'node_modules/@daytonaio/sdk': { version: transitive } } });
  const files = { '.git/HEAD': '', 'package-lock.json': lock, 'tools/x/package.json': manifest(`^${pinned}`), 'tools/x/index.ts': code };
  // When
  const { code: exit, report } = await run(files, [], 'tools/x');
  // Then: a workspace resolves through the lock; another project falls back to its manifest.
  expect(exit).toBe(workspace ? 2 : 0);
  expect(report.refused !== undefined).toBe(workspace);
});

it.each([
  ['bun.lock', `{\n  "lockfileVersion": 1,\n  "workspaces": {\n    "": {\n      "name": "root",\n    },\n  },\n  "packages": {\n    "@daytonaio/sdk": ["@daytonaio/sdk@${transitive}", "", {}, "a"],\n  },\n}\n`],
  ['yarn.lock', `__metadata:\n  version: 8\n\n"@daytonaio/sdk@npm:0.100.0":\n  version: 0.100.0\n\n"@daytonaio/sdk@npm:^${transitive}":\n  version: ${transitive}\n\n"root@workspace:.":\n  version: 0.0.0-use.local\n`],
])('a root %s does not resolve a nested project that is not a workspace', async (lockfile, text) => {
  // Given: tools/x is a separate project on 0.100.0 below a root lockfile on a newer minor.
  const files = { '.git/HEAD': '', [lockfile]: text, 'tools/x/package.json': manifest('0.100.0'), 'tools/x/index.ts': code };
  // When
  const { code: exit, report } = await run(files, [], 'tools/x');
  // Then: its own manifest stands in.
  expect(exit).toBe(0);
  expect(report.warnings).toEqual([{ file: 'package.json', message: olderMessage('0.100.0', pinned) }]);
});

it('a classic yarn.lock resolves only the workspaces its root manifest declares', async () => {
  // Given: the root's helper pulls ^pinned resolved to a newer minor; tools/ is a separate project.
  const yarn = `# yarn lockfile v1\n\n"@daytonaio/sdk@^${pinned}":\n  version "${transitive}"\n`;
  const files = { '.git/HEAD': '', 'yarn.lock': yarn, 'package.json': JSON.stringify({ name: 'root', workspaces: ['packages/*'], dependencies: { helper: '1.0.0' } }),
    'tools/package.json': manifest(`^${pinned}`), 'tools/index.ts': code };
  // When
  const { code: exit, report } = await run(files, [], 'tools');
  // Then: its own manifest stands in.
  expect(exit).toBe(0);
  expect(report.refused).toBeUndefined();
});

it.each(['0.0.0-use.local', `0.0.0-local`])('treats a local build %s as unknown', async version => {
  // Given
  const lock = JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/@daytonaio/sdk': { version } } });
  // When
  const { report } = await run({ 'package.json': manifest('workspace:*'), 'package-lock.json': lock, 'index.ts': code });
  // Then
  expect(report.warnings).toEqual([{ file: 'package.json', message: unknownMessage(pinned) }]);
});

it('treats a pnpm lockfile with a broken alias as no evidence', async () => {
  // Given
  const pnpm = `lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies: *missing\n`;
  // When
  const { code: exit, report } = await run({ 'package.json': manifest('0.100.0'), 'pnpm-lock.yaml': pnpm, 'index.ts': code });
  // Then
  expect(exit).toBe(0);
  expect(report.warnings).toEqual([{ file: 'package.json', message: olderMessage('0.100.0', pinned) }]);
});

it('keeps the version warning on an exit-1 report', async () => {
  // Given: an older 0.x version and an unreadable directory.
  const root = temporaryProject({ 'package.json': manifest('0.100.0'), 'index.ts': code, 'locked/a.ts': '' });
  chmodSync(join(root, 'locked'), 0o000);
  try {
    // When
    const result = await invoke(root, ['--json', '--dry']);
    // Then: both tools report it as Python does.
    expect(result.code).toBe(1);
    expect((JSON.parse(result.output) as Report).warnings).toEqual([{ file: 'package.json', message: olderMessage('0.100.0', pinned) }]);
  } finally { chmodSync(join(root, 'locked'), 0o755); }
});

it('reads only the head of a large pnpm lockfile', async () => {
  // Given: an 8 MB lockfile whose packages section dwarfs its importers.
  const packages = Array.from({ length: 40_000 }, (_, index) =>
    `  pkg-${index}@1.0.0:\n    resolution: {integrity: sha512-${'x'.repeat(150)}}\n`).join('');
  const pnpm = `lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      '@daytonaio/sdk':\n        specifier: ^0.100.0\n        version: 0.100.0\n\npackages:\n${packages}`;
  const root = temporaryProject({ 'package.json': manifest('^0.100.0'), 'pnpm-lock.yaml': pnpm, 'index.ts': code });
  const started = performance.now();
  // When
  const result = await invoke(root, ['--json', '--dry']);
  // Then
  expect(performance.now() - started).toBeLessThan(15_000);
  expect((JSON.parse(result.output) as Report).warnings).toEqual([{ file: 'pnpm-lock.yaml', message: olderMessage('0.100.0', pinned) }]);
}, 60_000);

it('refuses a partial root with no Daytona code as no-daytona, not by version', async () => {
  // Given: the root declares a newer minor; sub/ has no Daytona code.
  const { code: exit, report } = await run({ '.git/HEAD': '', 'package.json': manifest('0.999.0'), 'sub/x.ts': 'export const x = 1;\n' }, [], 'sub');
  // Then
  expect(exit).toBe(2);
  expect(report.refused?.reason).toBe('no-daytona');
  expect(report.warnings).toBeUndefined();
});

it('matches a classic yarn workspace glob written with ./', async () => {
  // Given: workspaces ["./packages/*"]; the lockfile resolves a newer minor for packages/a.
  const yarn = `# yarn lockfile v1\n\n"@daytonaio/sdk@^${pinned}":\n  version "${transitive}"\n`;
  const files = { '.git/HEAD': '', 'yarn.lock': yarn, 'package.json': JSON.stringify({ name: 'root', private: true, workspaces: ['./packages/*'] }),
    'packages/a/package.json': manifest(`^${pinned}`), 'packages/a/index.ts': code };
  // When
  const { code: exit, report } = await run(files, [], 'packages/a');
  // Then: the workspace's lockfile resolution counts.
  expect(exit).toBe(2);
  expect(report.refused?.message).toBe(newerRefusal(transitive, '../../yarn.lock', pinned));
});

// upgrade/mapping/v0-version-messages.json holds the expected text for both tools.
type SharedCase = { readonly id: string; readonly version: { readonly npm: string }; readonly flags?: readonly string[];
  readonly unknownFile?: boolean; readonly refusal?: string; readonly warning?: string };
const shared = (JSON.parse(readFileSync(join(here, '../../mapping/v0-version-messages.json'), 'utf8')) as { readonly cases: readonly SharedCase[] }).cases;
it.each(shared.map(entry => [entry.id, entry] as const))('prints the shared %s message', async (_, entry) => {
  // Given: package-lock.json resolves the case's version.
  const fill = (text: string): string => text.replaceAll('{pinned}', pinned).replaceAll('{newerPatch}', newerPatch).replaceAll('{file}', 'package-lock.json');
  const lock = JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/@daytonaio/sdk': { version: fill(entry.version.npm) } } });
  // When
  const { code: exit, report } = await run({ 'package.json': manifest('*'), 'package-lock.json': lock, 'index.ts': code }, entry.flags ?? []);
  // Then: each case names exactly one expectation (a misspelled key fails here).
  expect(Object.keys(entry).filter(key => key === 'refusal' || key === 'warning')).toHaveLength(1);
  if (entry.refusal !== undefined) {
    expect(exit).toBe(2);
    expect(report.refused?.message).toBe(fill(entry.refusal));
  } else {
    expect(exit).toBe(0);
    expect(report.refused).toBeUndefined();
    expect(report.warnings).toEqual([{ file: entry.unknownFile ? 'package.json' : 'package-lock.json', message: fill(entry.warning ?? '') }]);
  }
});

it('ignores commented-out bun.lock entries', async () => {
  // Given: a hand-added comment naming a newer minor.
  const bun = `{\n  "lockfileVersion": 1,\n  "packages": {\n    "@daytonaio/sdk": ["@daytonaio/sdk@${pinned}", "", {}, "a"],\n    // "@daytonaio/sdk": ["@daytonaio/sdk@${transitive}", "", {}, "x"],\n    /* "@daytonaio/sdk": ["@daytonaio/sdk@${transitive}", "", {}, "y"], */\n  },\n}\n`;
  // When
  const { code: exit, report } = await run({ 'package.json': manifest(`^${pinned}`), 'bun.lock': bun, 'index.ts': code });
  // Then
  expect(exit).toBe(0);
  expect(report.refused).toBeUndefined();
});

it('reads the last document of a pnpm lockfile with a boot document', async () => {
  // Given: pnpm writes its config dependencies first; the project's lockfile is the last document.
  const pnpm = `---\nlockfileVersion: '9.0'\nimporters:\n  .:\n    configDependencies: {}\npackages:\n  pnpm@10.0.0:\n    resolution: {integrity: a}\n---\nlockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      '@daytonaio/sdk':\n        specifier: '>=0.200.0'\n        version: ${transitive}\npackages:\n  '@daytonaio/sdk@${transitive}':\n    resolution: {integrity: b}\n`;
  // When
  const { code: exit, report } = await run({ 'package.json': manifest('>=0.200.0'), 'pnpm-lock.yaml': pnpm, 'index.ts': code });
  // Then: the lockfile's resolution, not the manifest's floor.
  expect(exit).toBe(2);
  expect(report.refused?.message).toBe(newerRefusal(transitive, 'pnpm-lock.yaml', pinned));
});

it('a classic yarn.lock with no root manifest owns no nested project', async () => {
  // Given: yarn.lock at the Git root, no root package.json, app/ declares 0.100.0.
  const yarn = `# yarn lockfile v1\n\n"@daytonaio/sdk@0.100.0":\n  version "${transitive}"\n`;
  // When
  const { report } = await run({ '.git/HEAD': '', 'yarn.lock': yarn, 'app/package.json': manifest('0.100.0'), 'app/index.ts': code }, [], 'app');
  // Then: its own manifest stands in.
  expect(report.refused).toBeUndefined();
  expect(report.warnings).toEqual([{ file: 'package.json', message: olderMessage('0.100.0', pinned) }]);
});

it('treats the 1.x shim release as unknown', async () => {
  // Given: package-lock.json resolves a 1.x release.
  const lock = JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/@daytonaio/sdk': { version: '1.0.1' } } });
  // When
  const { report } = await run({ 'package.json': manifest('^0.100.0'), 'package-lock.json': lock, 'index.ts': code });
  // Then: the lockfile's 1.x resolution, not the manifest's 0.x floor.
  expect(report.warnings).toEqual([{ file: 'package.json', message: unknownMessage(pinned) }]);
});

it('colliding declarations still give the installed version', async () => {
  // Given: a half-done rename declares both names, and the lockfile resolves a newer minor.
  const files = { 'package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': '0.100.0', '@daytona/sdk': '0.999.0' } }),
    'package-lock.json': JSON.stringify({ lockfileVersion: 3, packages: {
      'node_modules/@daytonaio/sdk': { version: '0.100.0' }, 'node_modules/@daytona/sdk': { version: '0.999.0' } } }), 'index.ts': code };
  // When
  const { code: exit, report, root } = await run(files);
  // Then: both declarations count (the older warns, the newer refuses), nothing written.
  expect(exit).toBe(2);
  expect(report.warnings).toEqual([{ file: 'package-lock.json', message: olderMessage('0.100.0', pinned) }]);
  expect(report.refused?.message).toBe(newerRefusal('0.999.0', 'package-lock.json', pinned));
  expect(readFileSync(join(root, 'index.ts'), 'utf8')).toBe(code);
});

it('checks a symlinked project where it lives', async () => {
  // Given: a workspace member reached through a symlink outside the repository.
  const root = temporaryProject({ 'repo/.git/HEAD': '',
    'repo/package-lock.json': JSON.stringify({ lockfileVersion: 3, packages: { 'packages/app': { name: 'app' }, 'node_modules/@daytonaio/sdk': { version: transitive } } }),
    'repo/packages/app/package.json': manifest(`^${pinned}`), 'repo/packages/app/index.ts': code });
  symlinkSync(join(root, 'repo/packages/app'), join(root, 'link'));
  // When
  const result = await invoke(join(root, 'link'), ['--json']);
  // Then: the real lockfile's resolution counts.
  expect(result.code).toBe(2);
  expect((JSON.parse(result.output) as Report).refused?.reason).toBe('newer-v0');
});

it('an unreadable own declaration is unknown, not an ancestor\'s version', async () => {
  // Given: the root declares a newer minor; app/ installs the SDK from Git.
  const { code: exit, report } = await run({ '.git/HEAD': '', 'package.json': manifest('0.999.0'),
    'app/package.json': manifest('github:daytonaio/sdk'), 'app/index.ts': code }, [], 'app');
  // Then: like the PyPI tool, app's own declaration is unknown.
  expect(exit).toBe(0);
  expect(report.warnings).toEqual([{ file: 'package.json', message: unknownMessage(pinned) }]);
});
