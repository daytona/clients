/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { file: 'index.ts', declaration: "import * as sdk from '@daytonaio/sdk';", inserted: "import * as sdkV0 from '@daytona/sdk/v0';" },
  { file: 'index.ts', declaration: "import sdk = require('@daytonaio/sdk');", inserted: "import sdkV0 = require('@daytona/sdk/v0');" },
  { file: 'index.js', declaration: "const sdk = require('@daytonaio/sdk');", inserted: "sdkV0 = require('@daytona/sdk/v0')" },
])('moves string-literal resource access to v0 when the namespace uses $declaration', async ({ file, declaration, inserted }) => {
  // Given
  const body = '\nconst resource = sdk["Sandbox"];\nconst root = sdk["Daytona"];\nconst version = sdk["v0"];\nconst other = sdk[name];\n';
  const root = temporaryProject({ [file]: declaration + body });
  // When
  await invoke(root);
  // Then
  const after = contents(root)[file] ?? '';
  expect(after).toContain(inserted);
  expect(after).toContain(body.replace('sdk["Sandbox"]', 'sdkV0["Sandbox"]'));
});

it('preserves an unrelated shadowed namespace when moving literal resource accesses', async () => {
  // Given
  const preserved = 'function use(sdk: Record<string, unknown>) { return sdk["Sandbox"]; }';
  const root = temporaryProject({ 'index.ts': `import * as sdk from '@daytonaio/sdk';\nconst resource = sdk['Sandbox'];\n${preserved}\n` });
  // When
  await invoke(root);
  // Then
  const after = contents(root)['index.ts'] ?? '';
  expect(after).toContain("const resource = sdkV0['Sandbox'];");
  expect(after).toContain(preserved);
});
