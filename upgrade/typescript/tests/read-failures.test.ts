/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { ReadFailures } from '../src/read-failures.js';

it.each(['EACCES', 'EPERM', 'EIO'])('reports a %s read failure with the appropriate description', code => {
  // Given
  const failures = new ReadFailures('/project');
  const error = Object.assign(new Error('device read failed'), { code });
  // When
  const value = failures.read('/project/src/main.ts', () => { throw error; });
  // Then
  expect(value).toBeUndefined();
  expect(failures.items).toEqual([{ file: 'src/main.ts', line: 0, column: 0, kind: 'unsupported-file',
    message: `src/main.ts could not be read: ${code === 'EIO' ? 'device read failed; fix the problem' : 'Permission denied; fix its permissions'}, then rerun.` }]);
});

it('keeps compiler probes for nonexistent files out of the report', () => {
  // Given
  const failures = new ReadFailures('/project');
  const error = Object.assign(new Error('not found'), { code: 'ENOENT' });
  // When / Then
  expect(() => failures.read('/project/missing.ts', () => { throw error; }, true)).toThrow(error);
  expect(failures.items).toEqual([]);
});

it('uses the human description when a read error includes a code, syscall and absolute path', () => {
  // Given
  const failures = new ReadFailures('/project');
  const error = Object.assign(new Error("ENOENT: no such file or directory, open '/project/src/main.ts'"), { code: 'ENOENT' });
  // When
  failures.read('/project/src/main.ts', () => { throw error; });
  // Then
  expect(failures.items).toEqual([{ file: 'src/main.ts', line: 0, column: 0, kind: 'unsupported-file',
    message: 'src/main.ts could not be read: No such file or directory; fix the problem, then rerun.' }]);
});
