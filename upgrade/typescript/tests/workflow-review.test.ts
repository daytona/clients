/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { parseMapping } from '../src/mapping.js';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

const mapping = parseMapping({ ...suggestionMapping, dataTraps: [
  { id: 'ids', text: 'Full ID migration guidance.', short: 'IDs change' },
  { id: 'storage', text: 'Review 0.x data dependencies' },
  { id: 'same-platform', text: 'Move every call together.', short: 'Same platform' },
] });
const setup = "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\n";

it('emits the compact unit header when a produced sandbox has no other calls', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': `${setup}const box = await d.v0.create();\n` });
  // When
  const result = await invoke(root, ['--suggest-v2'], mapping);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(`${setup}// daytona-v2: workflow \`box\`: move this sandbox to v2 as a unit
// daytona-v2:   data: IDs change; Review 0.x data dependencies
// daytona-v2:   docs: https://example.test/migrate#data
// daytona-v2: d.v2.create()
// daytona-v2:   differs: Choose a v2 image.
// daytona-v2:   docs: https://example.test/migrate#client-create
const box = await d.v0.create();
`);
});
it('preserves method order without repeating methods when a workflow has calls', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': `${setup}const box = await d.v0.create();\nawait box.fs.downloadFile('/file');\nawait box.delete();\nawait box.fs.downloadFile('/other');\n` });
  // When
  await invoke(root, ['--suggest-v2'], mapping);
  // Then
  const lines = contents(root)['index.ts']?.split('\n') ?? [];
  expect(lines.filter(line => line.startsWith('// daytona-v2: workflow'))).toEqual([
    '// daytona-v2: workflow `box`: move these v0 calls on it to v2 together: fs.downloadFile, delete',
  ]);
  expect(lines.filter(line => line.startsWith('// daytona-v2:   data:'))).toEqual([
    '// daytona-v2:   data: IDs change; Review 0.x data dependencies',
  ]);
});
it('deduplicates suggestion ids when several identical calls share one statement', async () => {
  // Given
  const call = 'await Promise.all([d.v0.list(), d.v0.list()]);\n';
  const root = temporaryProject({ 'index.ts': setup + call });
  // When
  await invoke(root, ['--suggest-v2'], mapping);
  // Then
  expect(contents(root)['index.ts']).toBe(`${setup}// daytona-v2: d.v2.list()
// daytona-v2:   docs: https://example.test/migrate#client-list
${call}`);
});
it('shares one data block when multiple producing calls belong to one statement', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': `${setup}const first = await d.v0.create(), second = await d.v0.create();\n` });
  // When
  await invoke(root, ['--suggest-v2'], mapping);
  // Then
  const lines = contents(root)['index.ts']?.split('\n') ?? [];
  expect(lines.filter(line => line.startsWith('// daytona-v2:   data:'))).toHaveLength(1);
  expect(lines.filter(line => line === '// daytona-v2:   docs: https://example.test/migrate#data')).toHaveLength(1);
  expect(lines.filter(line => line === '// daytona-v2: d.v2.create()')).toHaveLength(1);
});
it.each([
  { calls: '', message: 'workflow `box`: move this sandbox to v2 as a unit' },
  { calls: 'await box.delete();\n', message: 'workflow `box`: move these v0 calls on it to v2 together: delete' },
])('uses the header text as the JSON workflow message when calls are $calls', async ({ calls, message }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `${setup}const box = await d.v0.create();\n${calls}` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], mapping);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'workflow', message }),
  ]) });
});
