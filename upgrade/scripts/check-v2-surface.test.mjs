/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('./check-v2-surface.mjs', import.meta.url));
const receivers = {
  lspServer: { py: { types: ['LspServer', 'AsyncLspServer'] }, ts: { types: ['LspServer'] } },
  ptyHandle: { py: { types: ['PtyHandle', 'AsyncPtyHandle'] }, ts: { types: ['PtyHandle'] }, producedBy: ['connect-pty'] },
};

function runMapping(suggestions, context, files) {
  const directory = mkdtempSync(join(tmpdir(), 'v2-surface-'));
  context.after(() => rmSync(directory, { recursive: true, force: true }));
  const path = join(directory, 'mapping.json');
  writeFileSync(path, JSON.stringify({ receivers, suggestions }));
  let entryScript = script;
  if (files) {
    entryScript = join(directory, 'upgrade/scripts/check-v2-surface.mjs');
    mkdirSync(dirname(entryScript), { recursive: true });
    for (const name of ['check-v2-surface.mjs', 'optional-arguments.mjs', 'v2-typescript-surface.mjs', 'v2-python-surface.py']) {
      copyFileSync(new URL(name, import.meta.url), join(dirname(entryScript), name));
    }
    mkdirSync(join(directory, 'sdk-typescript'), { recursive: true });
    symlinkSync(fileURLToPath(new URL('../../sdk-typescript/node_modules', import.meta.url)), join(directory, 'sdk-typescript/node_modules'));
    writeFileSync(join(directory, 'sdk-typescript/tsconfig.json'), JSON.stringify({ compilerOptions: {
      strict: true, target: 'ES2022', module: 'ESNext', moduleResolution: 'Bundler', skipLibCheck: true,
    } }));
    for (const [name, text] of Object.entries(files)) {
      mkdirSync(dirname(join(directory, name)), { recursive: true });
      writeFileSync(join(directory, name), text);
    }
  }
  return spawnSync(process.execPath, [entryScript, '--mapping', path], { encoding: 'utf8' });
}

test('binds every current form when checked through the CLI', () => {
  // Given: the real mapping and in-repo SDK sources.
  // When: the same command used by CI runs.
  const result = spawnSync(process.execPath, [script], { encoding: 'utf8' });
  // Then: all non-null forms bind successfully.
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /^v2 surface bound: \d+ py forms, \d+ ts forms\n$/);
});

test('binds typed handles when forms contain local declarations and opaque user data', (context) => {
  // Given: free receivers, inferred locals, user inputs, callbacks and prose.
  const suggestions = [
    { id: 'user-data', v2: {
      py: '{sandbox}.git.get_config(f"remote.{name}.url", path=user_path)',
      ts: 'const handle = await {sandbox}.pty.start(undefined, { terminal: { cols, rows } }); await handle.attach()',
    } },
    { id: 'fork', v2: {
      py: '{client}.v2.get(id).fork(name=name, memory=False)',
      ts: '{client}.v2.get(id).then(source => source.fork({ name, memory: false }))',
    } },
    { id: 'lsp', receiver: 'lspServer', v2: { py: 'lsp.completions(uri, position)', ts: 'lsp.completions(uri, position)' } },
    { id: 'connect-pty', v2: { py: '{sandbox}.pty.connect(pty_id)', ts: '{sandbox}.pty.connect(ptyId)' } },
    { id: 'handle', receiver: 'ptyHandle', v2: { py: 'handle.wait()', ts: 'handle.wait()' } },
    { id: 'connection', receiver: 'ptyHandle', v2: { py: null, ts: 'connection.close()' } },
    { id: 'notes-only', v2: { py: null, ts: null }, notes: { py: ['set_lifecycle'], ts: ['sizeGb'] } },
  ];
  // When: checking expression forms, not prose or user argument types.
  const result = runMapping(suggestions, context);
  // Then: the typed chains bind without spurious user-data diagnostics.
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'v2 surface bound: 5 py forms, 6 ts forms\n');
});

test('rejects missing service chains when unrelated SDK methods still exist', (context) => {
  // Given: four methods on a service absent from the SDK.
  const suggestions = ['create', 'delete', 'get', 'list'].map((method) => ({
    id: `client.missingService.${method}`, receiver: 'client',
    v2: { py: `{client}.v2.missing_service.${method}(name)`, ts: `{client}.v2.missingService.${method}(name)` },
  }));
  // When: resolving the full receiver chain in both SDKs.
  const result = runMapping(suggestions, context);
  // Then: every row fails in both languages at the missing service member.
  assert.equal(result.status, 1, result.stderr);
  for (const entry of suggestions) for (const lang of ['py', 'ts']) {
    assert.ok(result.stderr.split('\n').some((line) => line.startsWith(`${entry.id} ${lang} `) && line.includes(lang === 'py' ? 'missing_service' : 'missingService')), result.stderr);
  }
});

test('rejects stale methods, parameters and overloads when the SDK has renamed them', (context) => {
  // Given: invalid surface spanning signature, syntax and receiver failures.
  const cases = [
    ['lifecycle-name', 'sandbox', '{sandbox}.set_lifecycle(idle_timeout_secs=interval)', '{sandbox}.setLifecycle({ idleTimeoutSecs: interval })'],
    ['lifecycle-key', 'sandbox', '{sandbox}.update_lifecycle_policy(timeout_secs=ttl)', "{sandbox}.updateLifecyclePolicy({ 'timeoutSecs': ttl })"],
    ['unknown-option', 'client', '{client}.v2.create(unknown_option=True)', '{client}.v2.create({ unknownOption: true })'],
    ['handle-method', 'ptyHandle', 'handle.to_dict()', "handle['missingV2Call']()"],
    ['unknown-receiver', undefined, 'mystery.run()', 'mystery.run()'],
    ['syntax', 'sandbox', '{sandbox}.pause(', '{sandbox}.pause('],
    ['wrong-overload', 'sandbox', null, '{sandbox}.stop(1, 2, 3)'],
    ['duplicate-keyword', 'sandbox', '{sandbox}.git.get_config(key, path=first, path=second)', null],
  ];
  const suggestions = cases.map(([id, receiver, py, ts]) => ({ id, receiver, v2: { py, ts } }));
  // When: one compiler program and Python AST index check the batch.
  const result = runMapping(suggestions, context);
  // Then: every invalid form is attributed to its row, language and source.
  assert.equal(result.status, 1, result.stderr);
  for (const entry of suggestions) for (const lang of ['py', 'ts']) {
    if (entry.v2[lang] === null) continue;
    assert.ok(result.stderr.split('\n').some((line) => line.startsWith(`${entry.id} ${lang} `)
      && line.endsWith(` | ${entry.v2[lang]}`)), result.stderr);
  }
});

const fixtureSdk = {
  'sdk-python/src/daytona/_client.py': `from .v2.daytona import AsyncDaytona as DaytonaV2Client
class Daytona:
    @property
    def v2(self) -> DaytonaV2Client:
        return DaytonaV2Client()
`,
  'sdk-python/src/daytona/v2/daytona.py': `class AsyncDaytona:
    async def create(self, *, region=None,
                     # commented_option=False,
                     ):
        pass
class Unrelated:
    async def create(self, *, commented_option=False):
        pass
`,
  'sdk-typescript/src/index.ts': `import { Client } from './v2/index.js';
export class Daytona { get v2(): Client { return new Client(); } }`,
  'sdk-typescript/src/v2/index.ts': `export class Client { create(options: { region?: string;
// commentedOption?: boolean;
}) {} }
export class Unrelated { create(options: { commentedOption?: boolean }) {} }
export class Sandbox {}`,
};

test('rejects commented-out parameters when another class still accepts the same name', (context) => {
  // Given: a synthetic SDK whose signature excludes a commented-out option.
  const entry = { id: 'commented', v2: {
    py: '{client}.v2.create(commented_option=True)', ts: '{client}.v2.create({ commentedOption: true })',
  } };
  // When: resolving the root property to the actual method declaration.
  const result = runMapping([entry], context, fixtureSdk);
  // Then: unrelated methods cannot bless a commented-out parameter.
  assert.equal(result.status, 1, result.stderr);
  for (const lang of ['py', 'ts']) assert.match(result.stderr, new RegExp(`commented ${lang} .*${lang === 'py' ? 'commented_option' : 'commentedOption'}`));
});

test('accepts visible parameters when they belong to the resolved class', (context) => {
  // Given: the same fixture with a valid option and arbitrary user value.
  const entry = { id: 'visible', v2: {
    py: '{client}.v2.create(region=region)', ts: '{client}.v2.create({ region })',
  } };
  // When: binding that signature through the root property.
  const result = runMapping([entry], context, fixtureSdk);
  // Then: both forms bind through their concrete receiver.
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'v2 surface bound: 1 py forms, 1 ts forms\n');
});
