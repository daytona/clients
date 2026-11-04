/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['undefined', 'void 0'])('migrates when %s explicitly selects default configuration', async argument => {
  // Given a valid explicit spelling of the constructor's default argument.
  const source = `import { Daytona } from '@daytonaio/sdk';\nnew Daytona(${argument}).create();\n`;
  const root = temporaryProject({ 'index.ts': source });
  // When applying the upgrade without a Cloud assertion.
  const result = await invoke(root);
  // Then the default argument cannot cause a custom-URL refusal.
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toContain(`new Daytona(${argument}).v0.create()`);
});

it.each([false, true])('refuses literal custom URL mutation atomically with drop=%s', async drop => {
  // Given a self-hosted config and an old dependency which must remain intact.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'index.ts': "import { Daytona } from '@daytonaio/sdk';\nconst cfg = {};\ncfg.apiUrl = 'https://self.example/api';\nnew Daytona(cfg).create();\n",
  });
  const before = contents(root);
  // When preflighting with or without the non-literal URL assertion.
  const result = await invoke(root, drop ? ['--drop-api-url'] : []);
  // Then the known custom URL always blocks every planned write.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it('refuses when a local undefined binding carries a custom URL', async () => {
  // Given a parameter hiding the intrinsic undefined value.
  const root = temporaryProject({
    'index.ts': "import { Daytona } from '@daytonaio/sdk';\nfunction run(undefined) { new Daytona(undefined).create(); }\nrun({ apiUrl: 'https://self.example/api' });\n",
  });
  const before = contents(root);
  // When preflighting the apparently default-valued constructor argument.
  const result = await invoke(root);
  // Then the shadowed name cannot bypass URL safety.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
