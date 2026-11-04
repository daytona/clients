/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { composeEnvFiles } from '../src/compose-env-files.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const services = ['services', "'services'", '"services"'];
const includes = ['include', "'include'", '"include"'];
const manifests = [
  ...services.flatMap(key => ['', '   '].map(space => `${key}${space}: {app: {env_file: runtime.conf}}`)),
  ...includes.flatMap(key => ['', '   '].map(space => `${key}${space}: [selected]`)),
  ...['', '\n', '# comment\n', '\n# comment\n\n---\n# document\n\n', '--- # document\n'].map(
    prefix => `${prefix}{services: {app: {env_file: runtime.conf}}}`),
  '# comment\n---\n{include: [selected]}',
];

it.each(manifests)('refuses custom env_file URLs for root formatting %j', async manifest => {
  // Given a nonstandard manifest filename requiring heuristic discovery.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'stack.yaml': manifest,
    selected: 'services: {app: {env_file: runtime.conf}}',
    'runtime.conf': 'DAYTONA_API_URL=https://self.example/api\n',
  });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the custom endpoint refuses the upgrade without modifying files.
  expect(result.code).toBe(2);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({
    kind: 'custom-api-url', file: 'runtime.conf',
  }));
  expect(contents(root)).toEqual(before);
});

it.each([
  'openapi: 3.0.0\npaths:\n  /services/include: {}',
  '# comment\n---\n{openapi: 3.0.0, paths: {}}',
  "'services-other': {}",
  '# comment\n---\ninclude: [{local: jobs.yml}]',
])('ignores non-Compose YAML with root formatting %j', manifest => {
  // Given an unrelated YAML document.
  const lines = manifest.split('\n');
  // When
  const result = composeEnvFiles(lines);
  // Then broader candidate detection does not imply Compose classification.
  expect(result).toEqual({ compose: false, references: [], includes: [] });
});
