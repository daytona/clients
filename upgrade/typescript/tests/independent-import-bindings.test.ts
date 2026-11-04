/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}';

it.each(["'create': c", "['create']: c", "['get']: c"])
('reports extracted methods when the binding key is %s', async key => {
  // Given a statically decoded moved member on a proven client.
  const binding = `const { ${key} } = client;`;
  const root = temporaryProject({ 'package.json': manifest, 'app.js': "import { Daytona } from '@daytonaio/sdk'; const client = new Daytona();\n" + binding });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then strict mode cannot certify the unbound extracted method.
  expect(result.code).toBe(1);
  expect(result.errors).toBe('');
  expect(contents(root)['app.js']).toContain(binding);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
});

it.each([
  "Daytona = (() => { const { Daytona, Sandbox } = require('@daytonaio/sdk'); return Daytona; })(), Sandbox",
  "Daytona = require('@daytonaio/sdk').Daytona, Sandbox",
  "Daytona, Sandbox = require('@daytonaio/sdk').Sandbox",
  "Daytona = require('@daytonaio/sdk').Daytona, Sandbox = require('@daytonaio/sdk').Sandbox",
])('composes nested CommonJS defaults when %s', async entries => {
  // Given an inner module edit inside a binding that must be split between root and v0.
  const root = temporaryProject({ 'app.js': `const { ${entries} } = require('@daytonaio/sdk');` });
  // When
  const result = await invoke(root, ['--json']);
  // Then the project migrates atomically without an internal overlap error.
  expect(result.code).toBe(0);
  expect(result.errors).toBe('');
  expect(contents(root)['app.js']).not.toContain('@daytonaio/sdk');
  expect(contents(root)['app.js']).toContain("require('@daytona/sdk')");
  expect(contents(root)['app.js']).toContain("require('@daytona/sdk/v0')");
});
