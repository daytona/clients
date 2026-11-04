/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { invoke, temporaryProject } from './helpers.js';

it.each([
  { type: 'FileSystem', attr: 'fs', method: 'listFiles', target: 'list' },
  { type: 'Git', attr: 'git', method: 'status', target: 'status' },
  { type: 'Process', attr: 'process', method: 'executeCommand', target: 'run' },
])('uses the sandbox owner when a $type service is aliased', async ({ attr, method, target }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytonaio/sdk';\nconst d = new Daytona();\nconst sb = await d.create();\nconst service = sb.${attr};\nconst alias = service;\nalias.${method}('x');\n` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], loadMapping());
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ suggestion: expect.objectContaining({ v2: expect.stringContaining(`sb.${attr}.${target}(`) }) })]) });
});

it.each([
  { type: 'FileSystem', method: 'listFiles', target: 'list' },
  { type: 'Git', method: 'status', target: 'status' },
  { type: 'Process', method: 'executeCommand', target: 'run' },
])('uses the service-level form when a $type parameter has no owner', async ({ type, method, target }) => {
  // Given
  const root = temporaryProject({ 'index.ts': `import type { ${type} } from '@daytonaio/sdk';\nfunction f(service: ${type}) { service.${method}('x'); }\n` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], loadMapping());
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ suggestion: expect.objectContaining({ v2: expect.stringMatching(new RegExp(`^service\\.${target}\\(`)) }) })]) });
});
