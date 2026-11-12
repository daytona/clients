/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' };
const prefix = "import { Daytona } from '@daytonaio/sdk';\nconst daytona = new Daytona({ apiKey: process.env.DAYTONA_API_KEY });\n";
const source = prefix + `async function ensure() {
  await daytona.snapshot.get('base');
  await daytona.volume.get('data', true);
  await daytona.secret.list();
  await daytona.warmPool.list();
}
export async function main() { await ensure(); await daytona.create({ snapshot: 'base' }); }
`;

it.each([false, true])('suggests all client service calls when a root method can run first and types=%s', async types => {
  // Given a direct SDK method call in a different execution scope.
  const root = temporaryProject({ ...manifest, 'app.ts': source });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then every mapped service remains proven, without manual-review omissions.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.output).toBe(0);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : [])).toEqual(
    expect.arrayContaining(['client.snapshot.get', 'client.volume.get', 'client.secret.list', 'client.warmPool.list', 'client.create']));
});

it('rewrites all client services when a root method can run first', async () => {
  // Given the same root binding across helper and entrypoint scopes.
  const root = temporaryProject({ ...manifest, 'app.ts': source });
  // When
  const result = await invoke(root, ['--json']);
  // Then the default pass agrees with the suggestion pass's client identity.
  expect(result.code, result.output).toBe(0);
  for (const service of ['snapshot', 'volume', 'secret', 'warmPool']) {
    expect(contents(root)['app.ts']).toContain(`daytona.v0.${service}.`);
  }
});

it.each([false, true])('lists client service calls in a workflow when another root method can run first and json=%s', async json => {
  // Given service calls passing a workflow's sandbox and a later-offset root call.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `async function workflow() {
  const sandbox = await daytona.create();
  await daytona.snapshot.create({ sandbox });
  await daytona.volume.get({ sandbox });
}
export async function main() { await workflow(); await daytona.list(); }
` });
  // When
  const result = await invoke(root, ['--suggest-v2', ...(json ? ['--json'] : [])]);
  // Then neither call disappears from the workflow group.
  const report: Report | undefined = json ? JSON.parse(result.output) : undefined;
  const workflow = json ? report?.items.find(item => item.kind === 'workflow')?.message :
    contents(root)['app.ts']?.split('\n').find(line => line.includes('workflow `sandbox`'));
  expect(workflow).toContain('daytona.snapshot.create');
  expect(workflow).toContain('daytona.volume.get');
});

it.each(['holder.inspect();', 'unknown(holder);'])('drops user-holder proofs when %s', async escape => {
  // Given a user-defined method or external call that can replace holder.client.
  const root = temporaryProject({ ...manifest, 'app.ts': `import { Daytona } from '@daytonaio/sdk';
const holder = { client: new Daytona(), inspect() { this.client = other; } };
${escape}
holder.client.create();
` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then a holder method is not mistaken for an SDK method.
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(1);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(contents(root)['app.ts']).not.toContain('\nholder.client.v0.create');
});

it.each(['unknown(daytona);', 'daytona.create(daytona);'])('suggests a root service call after the client is passed as an argument: %s', async escape => {
  // Given external code that receives the client before its service is used.
  const code = (v0: string) => prefix + `${escape.replace('daytona.create', `daytona${v0}.create`)} daytona${v0}.snapshot.get("base");`;
  // When: before and after the default pass, which moves the call to .v0 on the same client proof.
  const before: Report = JSON.parse((await invoke(temporaryProject({ ...manifest, 'app.ts': code('') }), ['--suggest-v2', '--json'])).output);
  const after: Report = JSON.parse((await invoke(temporaryProject({ ...manifest, 'app.ts': code('.v0') }), ['--suggest-v2', '--json'])).output);
  // Then both modes give the same suggestion, and no ownership FIXME.
  for (const report of [before, after]) {
    expect(report.items.some(item => item.suggestion?.id === 'client.snapshot.get')).toBe(true);
    expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  }
});

it('suggests service calls when a typed SDK parameter invokes a root method', async () => {
  // Given a parameter proven by the SDK class annotation, not its name.
  const root = temporaryProject({ ...manifest, 'app.ts': `import { Daytona } from '@daytonaio/sdk';
async function work(client: Daytona) { await client.create(); await client.snapshot.get('base'); }
` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then the SDK method does not fence its own service object.
  const report: Report = JSON.parse(result.output);
  expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : []))
    .toContain('client.snapshot.get');
});

it.each([
  'daytona.create = function () { this.snapshot = other; }; daytona.create();',
  'class Local extends Daytona { create() { this.snapshot = other; } } const local = new Local(); local.create();',
])('suggests the service call after a custom method effect: %s', async effect => {
  // Given a replaced SDK method or a user-defined subclass method.
  const receiver = effect.startsWith('class') ? 'local' : 'daytona';
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `${effect} ${receiver}.snapshot.get('base');` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then the service call is suggested as the default pass moves it (to
  // `.v0.snapshot`, the 0.x client's own service), with no ownership FIXME.
  const report: Report = JSON.parse(result.output);
  expect(report.items.some(item => item.suggestion?.id === 'client.snapshot.get')).toBe(true);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
});

it('flags the server-chosen volume type on volume suggestions', async () => {
  // Given 0.x volume calls, which always made disk volumes.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + "await daytona.volume.create('data');\nawait daytona.volume.get('data', true);\n" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then both suggestions keep the call without a type and ask for a review of it.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.output).toBe(0);
  const notes = new Map(report.items.flatMap(item => item.suggestion ? [[item.suggestion.id, item.suggestion.notes]] as const : []));
  expect(notes.get('client.volume.create')?.at(-1)).toBe(
    "Review the volume type: v0 volumes were always disk volumes, v2 lets the server choose, so pass type: 'disk' (with sizeGib) to keep a disk volume")
  expect(notes.get('client.volume.get')?.at(-1)).toBe(
    "Review the volume type: v0 volumes were always disk volumes, create: true lets the server choose, so to keep a disk volume call volumes.get(nameOrId) and, when it throws NotFoundError, volumes.create({ name, type: 'disk' })")
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([])
  // And the written comments carry it (a --json suggestion run writes nothing).
  expect((await invoke(root, ['--suggest-v2'])).code).toBe(0)
  expect(contents(root)['app.ts']).toContain('daytona-v2:   differs: Review the volume type')
});

it('points code_run chart artifacts at the v2 result', async () => {
  // Given a 0.x code run whose chart artifacts the caller reads.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + "const sandbox = await daytona.create();\nconst response = await sandbox.process.codeRun('plot()');\nconsole.log(response.artifacts?.charts);\n" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then the suggestion says where the charts and their images are in v2.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.output).toBe(0);
  const notes = report.items.find(item => item.suggestion?.id === 'sandbox.process.codeRun')?.suggestion?.notes;
  expect(notes?.at(-1)).toBe(
    "Returns InterpreterResult: response.artifacts.charts becomes result.charts, and each figure's image is in result.results[i].png; timeout becomes timeoutSeconds (still seconds)");
  // And the written comment carries it.
  expect((await invoke(root, ['--suggest-v2'])).code).toBe(0);
  expect(contents(root)['app.ts']).toContain('daytona-v2:   differs: Returns InterpreterResult: response.artifacts.charts becomes result.charts');
});
