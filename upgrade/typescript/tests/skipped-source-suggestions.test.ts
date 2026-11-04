/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { Project } from 'ts-morph';
import { afterEach, expect, it, vi } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

afterEach(() => vi.restoreAllMocks());

it.each(['bundle.min.js', 'long.js'])('does not parse speed-skipped %s when suggesting v2', async file => {
  // Given: a suggestion candidate plus unrelated source excluded for cost.
  const input = `const asset = '${'a'.repeat(11000)}'; const invalid = ;`;
  const root = temporaryProject({
    'app.ts': "import { Daytona } from '@daytona/sdk'; const d = new Daytona(); d.v0.create();",
    [file]: input,
  });
  const parse = vi.spyOn(Project.prototype, 'addSourceFileAtPath');
  // When: suggestions run without the upgrade-only custom-URL safety scan.
  const result = await invoke(root, ['--suggest-v2'], suggestionMapping);
  // Then: suggestions exist without loading the skipped file into an AST.
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toContain('daytona-v2:');
  expect(parse).toHaveBeenCalledWith(join(root, 'app.ts'));
  expect(parse).not.toHaveBeenCalledWith(join(root, file));
  expect(contents(root)[file]).toBe(input);
});
