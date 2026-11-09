/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

function fixture(context, { suggestions, dependencies = true }) {
  const root = mkdtempSync(join(tmpdir(), 'v2-note-surface-'));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'upgrade/scripts'), { recursive: true });
  for (const name of ['check-v2-surface.mjs', 'optional-arguments.mjs', 'v2-typescript-surface.mjs', 'v2-python-surface.py']) {
    copyFileSync(new URL(name, import.meta.url), join(root, 'upgrade/scripts', name));
  }
  mkdirSync(join(root, 'sdk-typescript/src/v2'), { recursive: true });
  if (dependencies) symlinkSync(fileURLToPath(new URL('../../sdk-typescript/node_modules', import.meta.url)), join(root, 'sdk-typescript/node_modules'));
  writeFileSync(join(root, 'sdk-typescript/tsconfig.json'), JSON.stringify({ compilerOptions: {
    strict: true, target: 'ES2022', module: 'ESNext', moduleResolution: 'Bundler', skipLibCheck: true,
  } }));
  writeFileSync(join(root, 'sdk-typescript/src/index.ts'), `import { Client } from './v2/index.js';
export class Daytona { get v2(): Client { return new Client(); } }`);
  writeFileSync(join(root, 'sdk-typescript/src/v2/index.ts'), `export class Client { create(options: { region: string }) {} }
export class Sandbox { read(path: string) {} }`);
  mkdirSync(join(root, 'sdk-python/src/daytona/v2'), { recursive: true });
  writeFileSync(join(root, 'sdk-python/src/daytona/_client.py'), `from .v2.daytona import AsyncDaytona
class Daytona:
    @property
    def v2(self) -> AsyncDaytona:
        pass
`);
  writeFileSync(join(root, 'sdk-python/src/daytona/v2/daytona.py'), `class AsyncDaytona:
    async def create(self, *, region):
        pass
`);
  writeFileSync(join(root, 'sdk-python/src/daytona/v2/sandbox.py'), `class AsyncSandbox:
    async def read(self, path):
        pass
`);
  const mapping = join(root, 'mapping.json');
  writeFileSync(mapping, JSON.stringify({ suggestions }));
  return spawnSync(process.execPath, [join(root, 'upgrade/scripts/check-v2-surface.mjs'), '--mapping', mapping], { encoding: 'utf8' });
}

for (const language of ['py', 'ts']) {
  for (const note of ['Use {client}.v2.renamed(region)', 'Use `sandbox.renamed` instead']) {
    test(`${language} rejects a renamed method in a note when its v2 form is absent: ${note}`, (context) => {
      // Given: migration advice whose only method reference no longer exists.
      const entry = { id: 'notes-only', v2: { py: null, ts: null }, notes: { [language]: [note] } };
      // When: the real CLI resolves the note against the SDK sources.
      const result = fixture(context, { suggestions: [entry] });
      // Then: the failure identifies the row, language and missing method.
      assert.equal(result.status, 1, result.stderr);
      assert.match(result.stderr, new RegExp(`notes-only.* ${language} .*renamed`));
    });
  }
}

test('accepts note references when arguments are illustrative and prose is not SDK code', (context) => {
  // Given: valid members, omitted required arguments and unrelated prose/data.
  const entry = { id: 'notes-only', v2: { py: null, ts: null }, notes: {
    py: ['Use {client}.v2.create(region=...) or `sandbox.read`', 'Ignore result.items, missing(), and requests.get(url)'],
    ts: ['Use {client}.v2.create({ region }) or `sandbox.read`', 'Ignore result.items, missing(), and http.get(url)'],
  } };
  // When: checking references rather than compiling natural-language examples.
  const result = fixture(context, { suggestions: [entry] });
  // Then: valid advice passes without false argument or prose diagnostics.
  assert.equal(result.status, 0, result.stderr);
});

test('prints an actionable hint when the TypeScript dependency is absent', (context) => {
  // Given: SDK sources without sdk-typescript/node_modules.
  // When: a contributor invokes the surface checker.
  const result = fixture(context, { suggestions: [], dependencies: false });
  // Then: it fails with one actionable line, not a module-loader stack trace.
  assert.equal(result.status, 1);
  assert.equal(result.stderr.trim().split('\n').length, 1, result.stderr);
  assert.match(result.stderr, /run 'npm ci' in sdk-typescript\/ first/);
});
