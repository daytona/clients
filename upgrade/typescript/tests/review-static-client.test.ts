/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  "import { Daytona } from '@daytonaio/sdk';\nDaytona.createAxiosInstance(5000);",
  "import { Daytona as D } from '@daytonaio/sdk';\nD['createAxiosInstance'](5000);",
  "import { Daytona } from '@daytonaio/sdk';\nconst D = Daytona;\nD.createAxiosInstance(5000);",
  "import * as sdk from '@daytonaio/sdk';\nsdk.Daytona.createAxiosInstance(5000);",
  "const { Daytona } = require('@daytonaio/sdk');\nDaytona.createAxiosInstance(5000);",
  "const Daytona = require('@daytonaio/sdk').Daytona;\nDaytona.createAxiosInstance(5000);",
  "const Daytona = (await import('@daytonaio/sdk')).Daytona;\nDaytona.createAxiosInstance(5000);",
  "import { Daytona } from '@daytonaio/sdk';\nconst factory = Daytona.createAxiosInstance;",
].flatMap(source => [false, true].map(installed => ({ source, installed }))))(
  'reports the removed static helper when installed=$installed: $source', async ({ source, installed }) => {
    // Given
    const root = temporaryProject({ 'index.ts': source });
    if (installed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--strict', '--json']);
    // Then
    expect(result.code).toBe(1);
    const report: Report = JSON.parse(result.output);
    expect(report.items).toContainEqual(expect.objectContaining({ kind: 'fixme', message: expect.stringContaining('createAxiosInstance') }));
    expect(contents(root)['index.ts']).toContain('FIXME(daytona-upgrade)');
    expect(contents(root)['index.ts']).not.toContain('.v0.createAxiosInstance');
  });

it('does not duplicate the static helper FIXME when rerun', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\nDaytona.createAxiosInstance();" });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(contents(root)).toEqual(before);
});

it('leaves unrelated static helpers unchanged when no SDK constructor is used', async () => {
  // Given
  const source = 'class Other { static createAxiosInstance() {} }\nOther.createAxiosInstance();';
  const root = temporaryProject({ 'index.ts': source, 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}' });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(source);
});
