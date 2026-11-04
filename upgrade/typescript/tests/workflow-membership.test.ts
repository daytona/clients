/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Mapping } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

const mapping: Mapping = { ...suggestionMapping, suggestions: [
  ...suggestionMapping.suggestions,
  ...([
    ['client', 'stop'], ['client', 'start'], ['client', 'delete'], ['client.snapshot', 'get'],
    ['sandbox', 'setLabels'], ['sandbox.process', 'executeCommand'], ['sandbox.fs', 'uploadFile'],
  ] as const).map(([receiver, method]) => ({
    id: `${receiver}.${method}`, receiver, v0: { ts: method }, v2: { ts: `${method}(...)` },
    category: 'direct' as const, notes: { ts: [] }, anchor: `${receiver}-${method}`, startsWorkflow: false,
  })),
] };
const setup = "import { Daytona } from '@daytona/sdk';\nconst daytona = new Daytona();\n";
const lifecycle = `${setup}const sandbox = await daytona.v0.create();
await sandbox.setLabels({ purpose: 'test' });
await daytona.v0.stop(sandbox);
await sandbox.process.executeCommand('true');
await daytona.v0.start({ sandbox });
await sandbox.fs.uploadFile('local', 'remote');
await daytona.v0.delete({ selected: sandbox });
await daytona.v0.snapshot.get({ sandbox });
await daytona.v0.stop(sandbox);
`;
const lifecycleMessage = 'workflow `sandbox`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: daytona.v0.stop, daytona.v0.start';

it('warns against moving a workflow when client lifecycle operations receive its sandbox', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': lifecycle });
  // When
  const result = await invoke(root, ['--suggest-v2'], mapping);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']?.split('\n').filter(line => line === `// daytona-v2: ${lifecycleMessage}`))
    .toEqual([`// daytona-v2: ${lifecycleMessage}`]);
});
it('uses the same client-inclusive membership when reporting a workflow as JSON', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': lifecycle });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'workflow', message: lifecycleMessage }),
  ]) });
});
it('counts deferred lifecycle calls when grouping a lifecycle workflow', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': `${setup}const sandbox = await daytona.v0.create();
const alias = sandbox;
const other = await daytona.v0.create();
await daytona.v0.stop(other);
await daytona.v0.start(alias);
await daytona.v0.delete(sandbox.id);
await daytona.v0.delete({ nested: { sandbox } });
await daytona.v0.stop([sandbox]);
await daytona.v0.stop({ ...sandbox });
async function later() {
  await daytona.v0.start(sandbox);
  await sandbox.setLabels({});
}
async function shadow(sandbox: Sandbox) {
  await daytona.v0.start(sandbox);
  await sandbox.setLabels({});
}
const callback = () => daytona.v0.delete(sandbox);
await daytona.v2.stop(sandbox);
await sandbox.delete();
` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'workflow', message: 'workflow `sandbox`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: daytona.v0.start' }),
  ]) });
});
it('excludes other bindings and nested arguments when grouping a workflow without lifecycle calls', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': `${setup}const sandbox = await daytona.v0.create();
const alias = sandbox;
const other = await daytona.v0.create();
await daytona.v0.stop(other);
await daytona.v0.delete(alias);
await daytona.v0.delete(sandbox.id);
await daytona.v0.delete({ nested: { sandbox } });
await daytona.v0.stop([sandbox]);
await daytona.v0.stop({ ...sandbox });
await sandbox.setLabels({});
` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'workflow', message: 'workflow `sandbox`: move these v0 calls on it to v2 together: setLabels' }),
  ]) });
});
it('preserves the callee text when client services receive the sandbox inside an object argument', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': `${setup}const sandbox = await daytona.v0.create();
const service = daytona.v0.snapshot;
await daytona . v0 . delete({ sandbox });
await service.get('snapshot', { resource: sandbox });
await service.get({ resource: sandbox });
` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'workflow', message: 'workflow `sandbox`: move these v0 calls on it to v2 together: daytona . v0 . delete, service.get' }),
  ]) });
});
