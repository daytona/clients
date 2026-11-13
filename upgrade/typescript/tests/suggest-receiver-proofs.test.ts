/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

type Report = { items: { line: number; kind: string; message: string; suggestion?: { id: string; v2: string | null } }[] };

async function suggest(files: Readonly<Record<string, string>>): Promise<Report> {
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.231.1"}}', ...files });
  const result = await invoke(root, ['--suggest-v2', '--json']);
  expect(result.code, result.output).toBe(0);
  return JSON.parse(result.output) as Report;
}

function ids(report: Report): string[] {
  return report.items.flatMap(item => item.kind === 'suggestion' && item.suggestion ? [item.suggestion.id] : []).sort();
}

it.each([
  ['a parameter', "async function f(sandbox: Sandbox) {\n", '}\n'],
  ['a created sandbox', 'const sandbox = await new Daytona().v0.create();\n', ''],
])('suggests later calls on %s after a root sandbox method', async (_name, open, close) => {
  // Given: 0.x sandbox methods that update the sandbox's data, then service calls on it.
  const report = await suggest({ 'index.ts': "import { Daytona, Sandbox } from '@daytonaio/sdk';\n" + open +
    'await sandbox.refreshData();\nawait sandbox.setLabels({});\nawait sandbox.archive();\n' +
    "await sandbox.fs.uploadFile(Buffer.from('x'), '/tmp/x');\nawait sandbox.git.status('/r');\nawait sandbox.process.executeCommand('ls');\n" + close });
  // Then: every call gets its suggestion.
  expect(ids(report)).toEqual(expect.arrayContaining(['sandbox.archive', 'sandbox.fs.uploadFile', 'sandbox.git.status',
    'sandbox.process.executeCommand', 'sandbox.refreshData', 'sandbox.setLabels']));
});

it('suggests root service calls the same way before and after the default pass', async () => {
  // Given: a client that escapes into a helper before its services are used.
  const program = (v0: string) => "import { Daytona } from '@daytonaio/sdk';\n" +
    'async function helper(daytona: Daytona) {}\nasync function main() {\n  const daytona = new Daytona();\n  await helper(daytona);\n' +
    `  const volume = await daytona${v0}.volume.get('v', true);\n  await daytona${v0}.volume.delete(volume);\n` +
    `  await daytona${v0}.snapshot.delete(await daytona${v0}.snapshot.get('s'));\n}\n`;
  // When: suggest on the 0.x source, and on what the default pass writes from it.
  const before = await suggest({ 'index.ts': program('') });
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.231.1"}}', 'index.ts': program('') });
  expect((await invoke(root, ['--json'])).code).toBe(0);
  const migrated = contents(root)['index.ts'] ?? '';
  expect(migrated).toBe(program('.v0').replace('@daytonaio/sdk', '@daytona/sdk'));
  const after = await suggest({ 'index.ts': migrated });
  // Then: no ownership FIXME, and both runs give the same rows.
  for (const report of [before, after]) expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(ids(before)).toEqual(['client.snapshot.delete', 'client.snapshot.get', 'client.volume.delete', 'client.volume.get']);
  expect(ids(after)).toEqual(ids(before));
});

it('reports every LSP server call with no v2 equivalent', async () => {
  // Given: 0.x LSP calls on a server from a known sandbox and on one whose sandbox is unknown.
  const report = await suggest({ 'index.ts': "import { LspServer, Sandbox } from '@daytonaio/sdk';\n" +
    "async function f(sandbox: Sandbox) {\n  const lsp = await sandbox.createLspServer('python', '/p');\n  await lsp.start();\n}\n" +
    'async function g(server: LspServer) {\n  await server.start();\n}\n' });
  // Then: v2 has no LSP service, so each call is a suggestion with no v2 form, never an ownership move.
  for (const [line, id] of [[3, 'sandbox.createLspServer'], [4, 'lspServer.start'], [7, 'lspServer.start']] as const) {
    expect(report.items.find(item => item.line === line)).toMatchObject({ kind: 'suggestion', suggestion: { id, v2: null } });
  }
});

it('drops service proofs after a sandbox method the code replaced', async () => {
  // Given a 0.x sandbox method the program overwrote with its own function.
  const report = await suggest({ 'index.ts': "import { Sandbox } from '@daytonaio/sdk';\n" +
    "async function f(sb: Sandbox, other: unknown) {\n  await sb.git.status('/r');\n" +
    "  sb.refreshData = async function (this: any) { this.fs = other };\n" +
    "  await sb.refreshData();\n  await sb.fs.createFolder('/x', '755');\n}\n" });
  // Then the call before the replacement is suggested, the later service call is not.
  expect(ids(report)).toContain('sandbox.git.status');
  expect(ids(report)).not.toContain('sandbox.fs.createFolder');
});

it('keeps a client.v0 escape to its own client, not another with the same name', async () => {
  // Given two functions with their own `client`; only the first hands its 0.x client out.
  const report = await suggest({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
    "async function f(client: Daytona) {\n  unknown(client.v0);\n  await client.v0.snapshot.get('a');\n}\n" +
    "async function g(client: Daytona) {\n  await client.v0.snapshot.get('b');\n}\n" });
  // Then only the second call is suggested.
  const lines = report.items.flatMap(item => item.suggestion?.id === 'client.snapshot.get' ? [item.line] : []);
  expect(lines).toEqual([7]);
});

it('keeps service proofs after sandbox lifecycle methods whose names the client also has', async () => {
  // Given
  const report = await suggest({ 'index.ts': "import { Sandbox } from '@daytonaio/sdk';\n" +
    "async function f(sb: Sandbox) {\n  await sb.start();\n  await sb.fs.createFolder('/x', '755');\n  await sb.stop();\n  await sb.git.status('/r');\n}\n" });
  // Then
  expect(ids(report)).toEqual(expect.arrayContaining(['sandbox.fs.createFolder', 'sandbox.git.status', 'sandbox.start', 'sandbox.stop']));
});

it('drops service proofs after a sandbox method a project subclass overrides', async () => {
  // Given
  const report = await suggest({ 'index.ts': "import { Sandbox } from '@daytonaio/sdk';\n" +
    "class Mine extends Sandbox {\n  async refreshData() { (this as any).fs = null; }\n}\n" +
    "async function f(sb: Sandbox) {\n  await sb.git.status('/r');\n  await sb.refreshData();\n  await sb.fs.createFolder('/x', '755');\n}\n" });
  // Then
  expect(ids(report)).toContain('sandbox.git.status');
  expect(ids(report)).not.toContain('sandbox.fs.createFolder');
});
