/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.skipIf(process.platform === 'win32').each(
  ['lexical', 'canonical'].flatMap(base => ['service', 'include', 'interpolation'].map(loader => ({ base, loader }))),
)('refuses custom $loader inputs relative to a symlinked manifest at its $base directory', async ({ base, loader }) => {
  // Given competing clean and custom inputs beside a manifest and its symlink.
  const selected = loader === 'service'
    ? 'services: { app: { env_file: runtime.conf } }'
    : loader === 'include'
      ? 'include: [nested/selected]\nservices: {}'
      : 'include: [{ path: nested/selected, env_file: runtime.conf }]\nservices: {}';
  const root = temporaryProject({
    'app.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n",
    'package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': '0.224.0' } }),
    'compose.yaml': 'include: [lexical/selected]',
    'canonical/selected': selected,
    'lexical/nested/selected': 'services: {}',
    'canonical/nested/selected': 'services: {}',
    'lexical/runtime.conf': 'UNRELATED=lexical',
    'canonical/runtime.conf': 'UNRELATED=canonical',
    ...(loader === 'include'
      ? { [`${base}/nested/selected`]: 'services: { app: { environment: { DAYTONA_API_URL: https://self.example/api } } }' }
      : { [`${base}/runtime.conf`]: 'DAYTONA_API_URL=https://self.example/api\n' }),
  });
  symlinkSync(join(root, 'canonical/selected'), join(root, 'lexical/selected'), 'file');
  const before = contents(root);

  // When
  const result = await invoke(root, ['--json']);

  // Then every existing interpretation participates in atomic refusal.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
