/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';

const manifest = { dependencies: { '@daytonaio/sdk': '0.224.0' } };
const includes = ['[{template: Jobs/Build.gitlab-ci.yml}]', '\n  - local: jobs.yml', '\n  - project: group/project', '\n  - remote: https://example.com/jobs.yml'];

it.each(['.gitlab-ci.yml', 'pipeline.yaml'].flatMap(file => includes.map(include => ({ file, include })) ))(
  'ignores non-Compose include $include in $file', async ({ file, include }) => {
    // Given GitLab include metadata, not a Compose loader.
    const root = temporaryProject({ 'package.json': JSON.stringify(manifest), [file]: `include: ${include}\n` });
    // When
    const result = await invoke(root, ['--strict', '--dry', '--json']);
    // Then strict mode has no spurious environment review.
    expect(result.code).toBe(0);
    expect(JSON.parse(result.output).items).toEqual([]);
  },
);

it('ignores .gitlab-ci.yml even when it has Compose-shaped keys unless selected', async () => {
  // Given a GitLab file with a services mapping and a missing include path.
  const root = temporaryProject({ 'package.json': JSON.stringify(manifest),
    '.gitlab-ci.yml': 'include: [missing.yaml]\nservices: {}' });
  // When
  const result = await invoke(root, ['--strict', '--dry', '--json']);
  // Then its filename takes precedence over heuristic discovery.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});

it.each(['-f .gitlab-ci.yml', 'COMPOSE_FILE', 'include'])(
  'honors explicit Compose selection of GitLab filenames through %s', async selection => {
    // Given an explicitly selected manifest despite its GitLab filename.
    const script = selection === 'COMPOSE_FILE' ? 'COMPOSE_FILE=.gitlab-ci.yml docker compose up' : `docker compose ${selection} up`;
    const root = temporaryProject({ 'package.json': JSON.stringify({ ...manifest, ...(selection === 'include' ? {} : { scripts: { start: script } }) }),
      ...(selection === 'include' ? { 'compose.yaml': 'include: [.gitlab-ci.yml]' } : {}),
      '.gitlab-ci.yml': 'services: {app: {env_file: runtime.conf}}',
      'runtime.conf': 'DAYTONA_API_URL=https://self.example/api' });
    // When
    const result = await invoke(root, ['--json']);
    // Then explicit selection still discovers the custom endpoint.
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url', file: 'runtime.conf' }));
  },
);

it.each(['[selected]', '[{path: selected}]', '\n  - path: selected\n    env_file: clean.conf\n    project_directory: .'])(
  'discovers include-only Compose files when entries use %s', async include => {
    // Given a nonstandard YAML filename containing only valid Compose include entries.
    const root = temporaryProject({ 'package.json': JSON.stringify(manifest), 'stack.yaml': `include: ${include}`,
      selected: 'services: {app: {env_file: runtime.conf}}', 'clean.conf': 'UNRELATED=value',
      'runtime.conf': 'DAYTONA_API_URL=https://self.example/api' });
    // When
    const result = await invoke(root, ['--json']);
    // Then include-only Compose discovery is preserved.
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
  },
);
