/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

for (const [language, declaration] of [
  ['py', '    async def _experimental_unmapped(self):\n        pass\n'],
  ['ts', '  _experimental_unmapped() {}\n'],
  ['ts', '  public _experimental_unmapped() {}\n'],
  ['ts', '  public _experimental_unmapped = async () => {};\n'],
  ['ts', '  _experimental_unmapped = () => {};\n'],
  ['ts', '  public readonly _experimental_unmapped: () => Promise<void> = async () => {};\n'],
  ['ts', '  public _experimental_unmapped = function () {};\n'],
  ['ts', '  public _experimental_unmapped: () => void;\n'],
  ['ts', '  public _experimental_unmapped: { (): void };\n'],
  ['ts', '  public _experimental_unmapped?: (value: string) => void;\n'],
  ['ts', '  public _experimental_unmapped =\n    async () => {};\n'],
  ['ts', '  public _experimental_unmapped = (() => {});\n'],
  ['ts', '  public _experimental_unmapped: (() => void);\n'],
  ['ts', '  public _experimental_unmapped = () => {};\n  private _privateField = () => {};\n  protected _privateProtected: () => void;\n'],
]) {
  test(`reports uncovered ${language} experimental methods when declared as ${declaration.trim()}`, (context) => {
    // Given: mapped classes plus one experimental method and an ordinary private helper.
    // When: the CLI checks the fixture against the unchanged mapping.
    const result = checkFixture(context, language, declaration);
    // Then: the experimental method requires a row, but private helpers stay excluded.
    assert.equal(result.status, 1);
    assert.ok(result.stderr.includes(`client._experimental_unmapped (${language}): expected one suggestion, found 0`), result.stderr);
    assert.doesNotMatch(result.stderr, /_private/);
    assert.doesNotMatch(result.stderr, /v0Version/);
  });
}

test('reports a v0Version that is not the pinned release', (context) => {
  // Given: a fully covered fixture whose mapping names another 0.x release.
  // When: the CLI checks it.
  const result = checkFixture(context, 'py', '', (mapping) => { mapping.v0Version = '0.1.0'; });
  // Then: only the stale pin is reported.
  const pinned = readFileSync(new URL('../../scripts/v0-sdk-version', import.meta.url), 'utf8').split('\n')[0].trim().replace(/^v/, '');
  assert.equal(result.status, 1);
  assert.equal(result.stderr.trim(), `v0Version: 0.1.0 is not the pinned ${pinned} (scripts/v0-sdk-version)`);
});

function checkFixture(context, language, declaration, mutate = () => {}) {
  const root = mkdtempSync(join(tmpdir(), 'v0-surface-'));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  const put = (name, text) => {
    mkdirSync(dirname(join(root, name)), { recursive: true });
    writeFileSync(join(root, name), text);
  };
  const mapping = JSON.parse(readFileSync(new URL('../mapping/mapping.json', import.meta.url), 'utf8'));
  for (const lang of ['py', 'ts']) mapping.movedClientMembers[lang] = mapping.receivers.client[lang].members.filter((name) => name !== 'close');
  mutate(mapping);
  put('upgrade/mapping/mapping.json', JSON.stringify(mapping));
  copyFileSync(new URL('../mapping/mapping.schema.json', import.meta.url), join(root, 'upgrade/mapping/mapping.schema.json'));
  mkdirSync(join(root, 'upgrade/scripts'));
  copyFileSync(new URL('check-v0-surface.mjs', import.meta.url), join(root, 'upgrade/scripts/check-v0-surface.mjs'));
  for (const name of ['root-ts-exports.mjs', 'v0-ts-exports.mjs']) {
    symlinkSync(fileURLToPath(new URL(name, import.meta.url)), join(root, 'upgrade/scripts', name));
  }
  for (const lang of ['py', 'ts']) {
    const classes = new Map();
    for (const metadata of Object.values(mapping.receivers)) {
      for (const name of metadata[lang].types) classes.set(name, metadata[lang].members);
    }
    const client = mapping.receivers.client[lang].types[0];
    const text = [...classes].map(([name, members]) => {
      const methods = members.map((method) => lang === 'py'
        ? `    def ${method}(self):\n        pass\n` : `  public ${method}() {}\n`).join('');
      const extra = lang === language && name === client ? declaration : '';
      return lang === 'py' ? `class ${name}:\n${methods}${extra}    def _private_helper(self):\n        pass\n`
        : `export class ${name} {\n${methods}${extra}  private _privateHelper() {}\n}\n`;
    }).join('\n');
    put(lang === 'py' ? 'v0/sdk-python/src/daytona/fixture.py' : 'v0/sdk-typescript/src/fixture.ts', text);
  }
  put('v0/sdk-python/src/daytona/__init__.py', `__all__ = ${JSON.stringify(mapping.v0Exports.py)}`);
  put('v0/sdk-typescript/src/index.ts', `export { ${mapping.v0Exports.ts.join(', ')} } from './exports'`);
  put('v0/sdk-typescript/src/exports.ts', mapping.v0Exports.ts.map((name) => `export const ${name} = {};`).join('\n'));
  for (const component of ['sdk-python', 'sdk-typescript', 'scripts']) {
    symlinkSync(fileURLToPath(new URL(`../../${component}`, import.meta.url)), join(root, component));
  }
  return spawnSync(process.execPath, [join(root, 'upgrade/scripts/check-v0-surface.mjs'), '--src', join(root, 'v0')], { encoding: 'utf8' });
}
