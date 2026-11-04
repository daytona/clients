/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const variants = [
  { setup: "const { jest } = require('@jest/globals');", spy: 'jest.spyOn' },
  { setup: "const { jest: t } = require('@jest/globals');", spy: 't.spyOn' },
  { setup: "const g = require('@jest/globals');", spy: 'g.jest.spyOn' },
  { setup: "const { vi } = require('vitest');", spy: 'vi.spyOn' },
  { setup: "const { vi: t } = require('vitest');", spy: 't.spyOn' },
  { setup: "const g = require('vitest');", spy: 'g.vi.spyOn' },
  { setup: "const { spyOn: spy } = require('vitest');", spy: 'spy' },
] as const;

it.each(variants)('moves proven spy targets when using $setup', async ({ setup, spy }) => {
  // Given
  const input = `const { Daytona } = require('@daytona/sdk');\n${setup}\nconst client = new Daytona();\n${spy}(client, 'get');\n`;
  const root = temporaryProject({ 'index.cjs': input });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.cjs']).toBe(input.replace(`${spy}(client,`, `${spy}(client.v0,`));
});

it.each(variants)('reports prototype targets when using $setup', async ({ setup, spy }) => {
  // Given
  const root = temporaryProject({ 'index.cjs': `const { Daytona } = require('@daytona/sdk');\n${setup}\n${spy}(Daytona.prototype, 'get');\n` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code).toBe(1);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'mock-target' }));
  expect(contents(root)['index.cjs']).toContain(`${spy}(Daytona.prototype, 'get')`);
});
