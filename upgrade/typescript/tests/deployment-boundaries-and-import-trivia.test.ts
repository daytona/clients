/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([false, true])('classifies deployment-named ancestors only outside a checkout, git=%s', async git => {
  // Given a project under deploy, but no deployment directory in the project.
  const parent = temporaryProject({
    'deploy/app/package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'deploy/app/values.yaml': 'DAYTONA_API_URL: https://daytona.internal.corp/api\n',
  });
  const root = join(parent, 'deploy/app');
  if (git) execFileSync('git', ['init', '-q', root]);
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then a checkout classifies from its own root; outside Git the whole path counts.
  expect(result.code).toBe(git ? 0 : 2);
  if (git) expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
});

it.each([false, true].flatMap(git => ['', 'deploy'].map(scan => ({ git, scan }))))(
  'refuses repository deployment URLs with git=$git and scan=$scan', async ({ git, scan }) => {
    // Given
    const root = temporaryProject({
      'deploy/package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'deploy/values.yaml': 'DAYTONA_API_URL: https://daytona.internal.corp/api\n',
    });
    if (git) execFileSync('git', ['init', '-q', root]);
    // When
    const result = await invoke(join(root, scan), ['--dry', '--json']);
    // Then narrowing the scan root never hides the deployment file.
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).refused).toMatchObject({ reason: 'custom-api-url' });
  });

it.each(['/* keep this */', '// keep this\n'])('preserves import trivia when all named imports move: %s', async comment => {
  // Given
  const root = temporaryProject({ 'index.ts': `import Daytona, ${comment} { Sandbox } from '@daytonaio/sdk';` });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain(comment);
  expect(contents(root)['index.ts']).toContain("import { Sandbox } from '@daytona/sdk/v0';");
});

it.each(['sandboxClient', 'SandboxCLIENT'])('leaves dynamic lookup clean for generic unresolved receiver %s', async name => {
  // Given
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytonaio/sdk';\ndeclare const ${name}: unknown;\n${name}[method]();` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toEqual([expect.objectContaining({ kind: 'manual-dependency' })]);
  expect(JSON.parse(result.output).items.filter((item: { readonly kind: string }) => item.kind === 'fixme')).toHaveLength(0);
  expect(contents(root)['index.ts']).toContain(`${name}[method]();`);
  expect(contents(root)['index.ts']).not.toContain('FIXME(daytona-upgrade):');
});

it.each(['new Daytona', 'new Daytona()'])('checks instance membership at runtime when the receiver is %s', async receiver => {
  // Given a root whose constructor has no v0 property, unlike its instances.
  const root = temporaryProject({ 'index.js': `import { Daytona } from '@daytonaio/sdk';\n'create' in ${receiver};` });
  // When
  await invoke(root);
  const output = contents(root)['index.js'] ?? '';
  // Then evaluate the migrated expression, not the original or an AST facsimile.
  const expression = output.slice(output.indexOf("'create' in"));
  expect(runInNewContext(`class Daytona { v0 = { create() {} }; }\n${expression}`)).toBe(true);
});

it('refuses a custom API URL before reporting a non-UTF-8 edited file', async () => {
  // Given
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    '.env': 'DAYTONA_API_URL=https://self.hosted/api\n',
  });
  writeFileSync(join(root, 'main.js'), Buffer.concat([Buffer.from("import { Daytona } from '@daytonaio/sdk'\nconst label = 'caf"), Buffer.from([0xe9]), Buffer.from("'\nnew Daytona().create()\n")]));
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).refused).toMatchObject({ reason: 'custom-api-url' });
});
