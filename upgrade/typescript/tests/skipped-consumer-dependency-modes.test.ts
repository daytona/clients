/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Interface } from 'node:readline/promises';
import { expect, it, vi } from 'vitest';
import { main } from '../src/cli.js';
import type { Report } from '../src/model.js';
import { contents, temporaryProject } from './helpers.js';

it.each(['dry', 'print', 'decline', 'write'])('reports a planned dependency update accurately when mode is %s', async mode => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
    'app.min.js': '/* Daytona */' });
  const before = contents(root);
  const tty = Object.getOwnPropertyDescriptor(process.stdin, 'isTTY');
  Object.defineProperty(process.stdin, 'isTTY', { configurable: true, value: true });
  const question = vi.spyOn(Interface.prototype, 'question').mockResolvedValue('n');
  const output: string[] = [];
  try {
    // When
    const code = await main([root, '--force', '--json', ...(mode === 'decline' ? [] : ['--yes']),
      ...(mode === 'dry' ? ['--dry'] : mode === 'print' ? ['--print'] : [])], { out: text => output.push(text), err: () => {} });
    // Then
    const report: Report = JSON.parse(output.join(''));
    expect(code).toBe(0);
    expect(report.items).toContainEqual(expect.objectContaining({ file: 'app.min.js', kind: 'unsupported-file',
      message: expect.stringContaining('The Daytona dependency is being updated to 2.x; this file needs manual migration.') }));
    if (mode !== 'write') expect(contents(root)).toEqual(before);
    else expect(JSON.parse(contents(root)['package.json'] ?? '{}')).toEqual({ dependencies: { '@daytona/sdk': '^2.0.0' } });
    expect(question).toHaveBeenCalledTimes(mode === 'decline' ? 1 : 0);
  } finally {
    question.mockRestore();
    if (tty) Object.defineProperty(process.stdin, 'isTTY', tty);
    else Reflect.deleteProperty(process.stdin, 'isTTY');
  }
});
