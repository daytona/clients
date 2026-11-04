/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const settings = [
  '    environment: {DAYTONA_API_URL: "https://self.hosted"}',
  '    environment: {DAYTONA_API_URL: "https://self.hosted", OTHER: ok} # trailing',
  "    environment: {'DAYTONA_SERVER_URL': 'https://self.hosted'} # trailing",
  '    environment: ["DAYTONA_API_URL=https://self.hosted"] # trailing',
  "    environment: ['DAYTONA_SERVER_URL=https://self.hosted', OTHER=ok] # trailing",
  '    environment:\n      DAYTONA_API_URL: https://self.hosted # trailing',
  "    environment:\n      'DAYTONA_API_URL': 'https://self.hosted' # trailing",
  '    environment:\n      - DAYTONA_API_URL=https://self.hosted # trailing',
  '    environment:\n      - "DAYTONA_API_URL=https://self.hosted" # trailing',
  "    environment:\n      - 'DAYTONA_API_URL=https://self.hosted' # trailing",
] as const;

it.each(settings)('refuses custom URLs without writing when Compose contains %s', async setting => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'compose.yaml': `services:\n  app:\n${setting}\n` });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' }, items: expect.arrayContaining([
    expect.objectContaining({ kind: 'env-api-url' }), expect.objectContaining({ kind: 'custom-api-url' }),
  ]) });
  expect(contents(root)).toEqual(before);
});

it.each(settings)('accepts Cloud values without mistaking YAML delimiters for URL text: %s', async setting => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'compose.yaml': `services:\n  app:\n${setting.replace('https://self.hosted', 'https://app.daytona.io/api')}\n` });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([expect.objectContaining({ kind: 'env-api-url' })]);
});
