/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

it.each([false, true].flatMap(before => [
  { deferred: 'async function later() { await box.stop(); }', callee: 'stop' },
  { deferred: 'const later = async () => box.pause();', callee: 'pause' },
  { deferred: 'const later = async function () { await d.v0.stop(box); };', callee: 'd.v0.stop' },
  { deferred: 'const later = async () => box.fs.listFiles("/");', callee: undefined, methods: 'fs.listFiles' },
  { deferred: 'const later = async () => d.v0.delete(box);', callee: undefined, methods: 'd.v0.delete' },
  { deferred: 'async function later() { const box = await d.v0.create(); await box.stop(); }', callee: undefined },
].map(entry => ({ methods: undefined, ...entry, before }))))('counts deferred calls by binding when before=$before and the workflow contains $deferred', async ({ deferred, callee, methods, before }) => {
  // Given
  const root = temporaryProject({ 'app.ts': `import { Daytona } from '@daytona/sdk'; const d = new Daytona();
async function workflow() {
${before ? deferred : ''}
const box = await d.v0.create();
${before ? '' : deferred}
}` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  const outer = report.items.find(item => item.kind === 'workflow' && item.line === 4);
  expect(outer?.message).toBe(callee === undefined ? methods === undefined ? 'workflow `box`: move this sandbox to v2 as a unit' :
    `workflow \`box\`: move these v0 calls on it to v2 together: ${methods}` :
    `workflow \`box\`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: ${callee}`);
});
