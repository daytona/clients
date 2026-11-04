/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const mapping = loadMapping();
const setup = "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\n";

it.each(['start', 'stop', 'pause', 'archive', 'setAutostopInterval', 'setAutoPauseInterval', 'setAutoArchiveInterval'])(
  'names the workflow and lifecycle call when the sandbox calls %s', async method => {
    // Given
    const root = temporaryProject({ 'app.ts': `${setup}const box = await d.v0.create();\nawait box.${method}();\n` });
    // When
    const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
    // Then
    const report: Report = JSON.parse(result.output);
    const message = `workflow \`box\`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: ${method}`;
    expect(result.code).toBe(0);
    expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([message]);
  });

it.each([
  { call: 'd.v0.start(box)', callee: 'd.v0.start' },
  { call: 'd.v0.stop(box)', callee: 'd.v0.stop' },
  { call: 'd.v0.start({ box })', callee: 'd.v0.start' },
  { call: 'd.v0.stop({ sandbox: box })', callee: 'd.v0.stop' },
])('names the client call when it receives the sandbox through $call', async ({ call, callee }) => {
  // Given
  const root = temporaryProject({ 'app.ts': `${setup}const box = await d.v0.create();\nawait ${call};\n` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([
    `workflow \`box\`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: ${callee}`,
  ]);
});

it.each([true, false])('lists only lifecycle calls in source order when a mixed workflow uses JSON=%s', async json => {
  // Given
  const root = temporaryProject({ 'app.ts': `${setup}const box = await d.v0.create();
await box.setAutoDeleteInterval(10);
await d.v0.stop(box);
await box.computerUse.start();
await box.archive();
await box.fs.downloadFile('/file');
await box.resume();
await box.computerUse.recording.start();
await d.v0.start({ sandbox: box });
await box.setAutoPauseInterval(10);
await box.computerUse.stop();
await box.delete();
` });
  // When
  const result = await invoke(root, json ? ['--suggest-v2', '--json'] : ['--suggest-v2'], mapping);
  // Then
  const message = 'workflow `box`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: d.v0.stop, archive, d.v0.start, setAutoPauseInterval';
  expect(result.code).toBe(0);
  if (json) {
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([message]);
  } else {
    expect(contents(root)['app.ts']?.split('\n').filter(line => line.startsWith('// daytona-v2: workflow')))
      .toEqual([`// daytona-v2: ${message}`]);
  }
});

it('keeps normal workflow guidance when the only call is an unmapped resume', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': `${setup}const box = await d.v0.create();\nawait box.resume();\n` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  expect(result.code).toBe(0);
  const report: Report = JSON.parse(result.output);
  expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([
    'workflow `box`: move this sandbox to v2 as a unit',
  ]);
});

it.each(['setAutoDeleteInterval', 'computerUse.start', 'computerUse.stop', 'computerUse.recording.start'])(
  'keeps normal workflow guidance when the only call is %s', async method => {
    // Given
    const root = temporaryProject({ 'app.ts': `${setup}const box = await d.v0.create();\nawait box.${method}();\n` });
    // When
    const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
    // Then
    const report: Report = JSON.parse(result.output);
    expect(result.code).toBe(0);
    expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([
      `workflow \`box\`: move these v0 calls on it to v2 together: ${method}`,
    ]);
  });

it('keeps each workflow call list separate when two sandboxes have lifecycle calls', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': `${setup}const first = await d.v0.create();
const second = await d.v0.create();
await second.pause();
await first.stop();
await d.v0.start(second);
await first.archive();
` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([
    'workflow `first`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: stop, archive',
    'workflow `second`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: pause, d.v0.start',
  ]);
});
