/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it, vi } from 'vitest';
import type { ts } from 'ts-morph';
import { compareRank, configRanks } from '../src/config-ownership.js';

// Exercise Windows path semantics on every host, without mocking the rank logic.
vi.mock('node:path', async importOriginal => {
  const path = await importOriginal<typeof import('node:path')>();
  return { ...path, dirname: path.win32.dirname, resolve: path.win32.resolve };
});

it.each(['\\', '/'])('prefers a deeper config when Windows paths use %s separators', separator => {
  // Given equal-priority configs, with no includes to influence their rank.
  const parsed: ts.ParsedCommandLine = { options: {}, fileNames: ['C:/repo/src/main.ts'], errors: [], raw: { files: [] } };
  const shallow = ['C:', 'repo', 'tsconfig.app.json'].join(separator);
  const deep = ['C:', 'repo', 'src', 'tsconfig.app.json'].join(separator);
  // When
  const shallowRank = [...configRanks(shallow, parsed).values()][0] ?? [];
  const deepRank = [...configRanks(deep, parsed).values()][0] ?? [];
  // Then directory distance breaks the tie in favor of the nearer config.
  expect(compareRank(deepRank, shallowRank)).toBeGreaterThan(0);
});
