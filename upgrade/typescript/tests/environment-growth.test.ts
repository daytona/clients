/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { appendFileSync, fstatSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { environmentText, MAX_ENVIRONMENT_BYTES } from '../src/environment-text.js';
import { temporaryProject } from './helpers.js';

vi.mock('node:fs', async importOriginal => {
  const original = await importOriginal<typeof import('node:fs')>();
  return { ...original, fstatSync: vi.fn(original.fstatSync) };
});
afterEach(() => { vi.mocked(fstatSync).mockRestore(); });

it('reads an appended custom URL when the file grows after fstat', () => {
  // Given a real file appended immediately after its descriptor size is captured.
  const initial = 'UNRELATED=value\n';
  const appended = 'DAYTONA_API_URL=https://self.example/api\n';
  const path = join(temporaryProject({ '.env': initial }), '.env');
  vi.mocked(fstatSync).mockImplementationOnce(() => {
    const stat = statSync(path);
    appendFileSync(path, appended);
    return stat;
  });
  // When
  const result = environmentText(path);
  // Then the appended endpoint is visible, not falsely certified as absent.
  expect(result).toEqual({ text: initial + appended, truncated: false });
});

it('reports truncation when a file grows beyond the scan limit after fstat', () => {
  // Given a small file that grows past the limit before the first read.
  const path = join(temporaryProject({ '.env': '# initial\n' }), '.env');
  vi.mocked(fstatSync).mockImplementationOnce(() => {
    const stat = statSync(path);
    appendFileSync(path, '#'.repeat(MAX_ENVIRONMENT_BYTES));
    return stat;
  });
  // When
  const result = environmentText(path);
  // Then unseen bytes require review and the cut-off line is discarded.
  expect(result).toEqual({ text: '# initial', truncated: true });
});

it.each([MAX_ENVIRONMENT_BYTES - 1, MAX_ENVIRONMENT_BYTES, MAX_ENVIRONMENT_BYTES + 1])(
  'reports the exact truncation boundary for %i bytes', size => {
    // Given a file at the byte-budget boundary.
    const path = join(temporaryProject({ '.env': '#'.repeat(size) }), '.env');
    // When
    const result = environmentText(path);
    // Then only bytes exceeding the budget count as truncation.
    expect(result?.truncated).toBe(size > MAX_ENVIRONMENT_BYTES);
  },
);
