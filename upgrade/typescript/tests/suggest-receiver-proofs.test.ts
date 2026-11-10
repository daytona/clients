/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';

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
  // When
  const before = await suggest({ 'index.ts': program('') });
  const after = await suggest({ 'index.ts': program('.v0') });
  // Then: no ownership FIXME, and both runs give the same rows.
  expect(before.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(ids(before)).toEqual(['client.snapshot.delete', 'client.snapshot.get', 'client.volume.delete', 'client.volume.get']);
  expect(ids(after)).toEqual(ids(before));
});

it('renders an LSP server start on the sandbox that created the server', async () => {
  // Given
  const report = await suggest({ 'index.ts': "import { LspServer, Sandbox } from '@daytonaio/sdk';\n" +
    "async function f(sandbox: Sandbox) {\n  const lsp = await sandbox.createLspServer('python', '/p');\n  await lsp.start();\n}\n" +
    'async function g(server: LspServer) {\n  await server.start();\n}\n' });
  // Then: the owner is traced back through createLspServer; an unproven owner is a manual move.
  expect(report.items.find(item => item.line === 4)?.suggestion?.v2).toBe('const lsp = await sandbox.lsp.start(languageId, pathToProject)');
  expect(report.items.find(item => item.line === 7)).toMatchObject({ kind: 'fixme',
    message: 'move this call to the owning sandbox: const lsp = await sandbox.lsp.start(languageId, pathToProject)' });
});

it('drops service proofs after a sandbox method the code replaced', async () => {
  // Given a 0.x sandbox method the program overwrote with its own function.
  const report = await suggest({ 'index.ts': "import { Sandbox } from '@daytonaio/sdk';\n" +
    "async function f(sb: Sandbox, other: unknown) {\n  sb.refreshData = async function (this: any) { this.fs = other };\n" +
    "  await sb.refreshData();\n  await sb.fs.createFolder('/x', '755');\n}\n" });
  // Then the later service call is not suggested as the SDK's.
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
