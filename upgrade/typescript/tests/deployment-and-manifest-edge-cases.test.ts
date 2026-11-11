/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['', 'deploy'])('refuses a custom deployment URL when scanning %s', async subdirectory => {
  // Given
  const root = temporaryProject({
    'deploy/package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'deploy/index.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();",
    'deploy/values.yaml': 'DAYTONA_API_URL: https://daytona.internal.corp/api\n',
  });
  const before = contents(root);
  execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(join(root, subdirectory), ['--dry', '--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).refused).toMatchObject({ reason: 'custom-api-url' });
  expect(contents(root)).toEqual(before);
});

it.each(['requirements.in', 'requirements-dev.in', 'constraints.in', 'constraints-prod.in', 'index.html.in'])('classifies Python requirements files conservatively: %s', async file => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}', [file]: '<html></html>\n' });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(file === 'index.html.in' ? 0 : 1);
  expect(JSON.parse(result.output).items.filter((entry: { readonly kind: string }) => entry.kind === 'other-ecosystem')).toHaveLength(file === 'index.html.in' ? 0 : 1);
});

it.each(['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'].flatMap(section => [
  'workspace:*', 'file:../sdk', 'link:../sdk', 'git+https://example.com/sdk.git', 'github:custom/sdk',
].map(spec => ({ section, spec }))))('preserves custom dependency $section: $spec', async ({ section, spec }) => {
  // Given
  const manifest = { [section]: { '@daytonaio/sdk': spec } };
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest) });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toEqual(manifest);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency' }));
});

it.each(['**/@daytonaio/sdk', '**/@daytona/sdk'])('reports scoped Yarn resolutions: %s', async selector => {
  // Given
  const resolutions = { [selector]: '0.224.0' };
  const root = temporaryProject({ 'package.json': JSON.stringify({ resolutions }) });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(contents(root)['package.json'] ?? '')).toEqual({ resolutions });
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency' }));
});

it('removes empty named-import braces after moving all named resources', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import Daytona, { Sandbox } from '@daytonaio/sdk';" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain("import Daytona from '@daytona/sdk';");
  expect(contents(root)['index.ts']).toContain("import { Sandbox } from '@daytona/sdk/v0';");
});

it.each([
  { before: "new Daytona({ apiUrl: 'https://app.daytona.io/api' })", after: 'new Daytona()' },
  { before: 'new Daytona({})', after: 'new Daytona({})' },
  { before: "new Daytona({ apiUrl: 'https://app.daytona.io/api', apiKey })", after: 'new Daytona({ apiKey })' },
])('only removes constructor objects emptied by migration: $before', async ({ before, after }) => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\n" + before });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain(after);
});

it('preserves an emptied argument when a subclass constructor can observe its presence', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytonaio/sdk';
class Custom extends Daytona {
  constructor(config = { apiKey: 'fallback' }) { super(config); }
}
new Custom({ apiUrl: 'https://app.daytona.io/api' });
` });
  // When
  await invoke(root, ['--drop-api-url']);
  // Then
  expect(contents(root)['index.ts']).toContain('new Custom({  });');
});
