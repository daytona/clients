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

it.each(['unknown(daytona);', 'daytona.create(daytona);'])('drops SDK sub-object proofs when the client is passed as an argument: %s', async escape => {
  // Given external code that can replace the SDK client's sub-objects.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `${escape} daytona.snapshot.get("base");` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then suggestions never invent a proven service receiver after an escape.
  const report: Report = JSON.parse(result.output);
  expect(report.items.some(item => item.suggestion?.id === 'client.snapshot.get')).toBe(false);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
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
])('keeps custom method effects when %s', async effect => {
  // Given a replaced SDK method or a user-defined subclass method.
  const receiver = effect.startsWith('class') ? 'local' : 'daytona';
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `${effect} ${receiver}.snapshot.get('base');` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then a custom implementation never gains the SDK method exemption.
  const report: Report = JSON.parse(result.output);
  expect(report.items.some(item => item.suggestion?.id === 'client.snapshot.get')).toBe(false);
  expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
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
  expect(notes.get('client.volume.get')?.at(-1)).toMatch(/^Review the volume type: v0 volumes were always disk volumes.*type: 'disk'/)
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([])
  // And the written comments carry it (a --json suggestion run writes nothing).
  expect((await invoke(root, ['--suggest-v2'])).code).toBe(0)
  expect(contents(root)['app.ts']).toContain('daytona-v2:   differs: Review the volume type')
});
