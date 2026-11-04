/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const settings = [
  'env: [{name: DAYTONA_API_URL, value: ENDPOINT}]',
  'env:\n  - {name: DAYTONA_API_URL, value: "ENDPOINT"}',
  "env: [{name: 'DAYTONA_SERVER_URL', value: 'ENDPOINT', other: ok}] # trailing",
] as const;

it.each(settings)('refuses custom URLs without writing when Kubernetes uses %s', async setting => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'k8s/app.yaml': setting.replace('ENDPOINT', 'https://self.hosted') });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
  expect(contents(root)).toEqual(before);
});

it.each(settings)('accepts Cloud URLs when Kubernetes uses %s', async setting => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'k8s/app.yaml': setting.replace('ENDPOINT', 'https://app.daytona.io/api') });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([expect.objectContaining({ kind: 'env-api-url' })]);
});
