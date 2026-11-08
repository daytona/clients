/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { envUrlItems } from '../src/env-api-url.js';
import { loadMapping } from '../src/mapping.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it('moves a bare import type with no qualifier to v0', async () => {
  // Given a type reference that names the whole SDK module, not a member.
  const root = temporaryProject({ 'app.ts': "type T = import('@daytonaio/sdk');\n" });
  // When the project is upgraded.
  await invoke(root, ['--json']);
  // Then the module specifier moves to the v0 subpath.
  expect(contents(root)['app.ts']).toBe("type T = import('@daytona/sdk/v0');\n");
});

it('keeps a typeof import type reference at the package root', async () => {
  // Given a typeof query over the whole SDK module.
  const root = temporaryProject({ 'app.ts': "type T = typeof import('@daytonaio/sdk');\n" });
  // When the project is upgraded.
  await invoke(root, ['--json']);
  // Then the module specifier stays at the package root, not v0.
  expect(contents(root)['app.ts']).toBe("type T = typeof import('@daytona/sdk');\n");
});

it('names the consumer package.json in a catalog manual-dependency message', async () => {
  // Given a workspace package pinned through a pnpm catalog entry.
  const root = temporaryProject({ 'packages/app/package.json': JSON.stringify({ dependencies: { '@daytonaio/sdk': 'catalog:' } }),
    'pnpm-workspace.yaml': "packages:\n  - packages/*\ncatalog:\n  '@daytonaio/sdk': '0.224.0'\n" });
  // When the project is upgraded.
  const result = await invoke(root, ['--json']);
  // Then the item is reported at the catalog file, and its message names the consuming manifest.
  const items = JSON.parse(result.output).items.filter((item: { kind: string }) => item.kind === 'manual-dependency');
  expect(items).toEqual([expect.objectContaining({ file: 'pnpm-workspace.yaml',
    message: 'Update Daytona package constraint @daytonaio/sdk manually for SDK 2.x (referenced from packages/app/package.json).' })]);
});

it('names the setting in the ignored-env message regardless of which name was read', () => {
  // Given a named environment setting other than DAYTONA_API_URL.
  const context = { mapping: loadMapping(), dropApiUrl: false };
  // When a diagnostic item is built for it.
  const items = envUrlItems({ file: '.env', line: 1, column: 1 }, { name: 'DAYTONA_SERVER_URL', literal: undefined }, context);
  // Then the message names that setting and the one variable .v2 reads.
  expect(items[0]?.message).toBe(
    'DAYTONA_SERVER_URL is ignored by SDK 2.x: .v0 targets Daytona Cloud, and .v2 reads only DAYTONA_V2_API_URL, for test environments. Review this setting.');
});
