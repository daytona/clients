/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import type { Report } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

const mapping = loadMapping();
const setup = "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\nconst sandbox = await d.v0.create();\n";
const lifecycle = 'workflow `sandbox`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: ';

it.each(['computerUse.recording.start', 'computerUse.start', 'computerUse.stop'])(
  'uses normal workflow guidance when a nested service calls %s', async method => {
    // Given
    const root = temporaryProject({ 'app.ts': `${setup}await sandbox.${method}();\n` });
    // When
    const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
    // Then
    const report: Report = JSON.parse(result.output);
    expect(result.code).toBe(0);
    expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([
      `workflow \`sandbox\`: move these v0 calls on it to v2 together: ${method}`,
    ]);
  });

it.each([
  { call: 'sandbox.stop()', method: 'stop' },
  { call: 'd.v0.start(sandbox)', method: 'd.v0.start' },
  { call: 'd.v0.stop(sandbox)', method: 'd.v0.stop' },
])('uses lifecycle workflow guidance when lifecycle call is $call', async ({ call, method }) => {
  // Given
  const root = temporaryProject({ 'app.ts': `${setup}await ${call};\n` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items.filter(item => item.kind === 'workflow').map(item => item.message)).toEqual([lifecycle + method]);
});

it.each(['', '// daytona-v2: // FIXME(daytona-upgrade): move this call to the owning sandbox: sandbox.pty.list()\n'])(
  'retains an ownerless service FIXME byte-identically when suggestions run again after %j', async previous => {
  // Given
  const root = temporaryProject({
    'app.ts': `import type { Process } from '@daytonaio/sdk';\nfunction run(process: Process) {\n${previous}  process.listPtySessions();\n}\n`,
    'node_modules/@daytonaio/sdk/package.json': '{"name":"@daytonaio/sdk","version":"0.224.0","types":"index.d.ts"}',
    'node_modules/@daytonaio/sdk/index.d.ts': 'export declare class Process { listPtySessions(): void; }',
  });
  const first = await invoke(root, ['--suggest-v2']);
  const before = readFileSync(join(root, 'app.ts'));
  expect(first.code).toBe(0);
  expect(before.toString()).toMatch(/^\s*\/\/ FIXME\(daytona-upgrade\): move this call to the owning sandbox:/m);
  // When
  const second = await invoke(root, ['--suggest-v2']);
  // Then
  expect(second.code).toBe(0);
  expect(readFileSync(join(root, 'app.ts'))).toEqual(before);
});
