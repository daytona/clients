/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Interface } from 'node:readline/promises';
import { expect, it, vi } from 'vitest';
import { main } from '../src/cli.js';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([false, true].flatMap(strict => [false, true].map(manual => ({ strict, manual }))))(
  'preserves strict manual-item status when confirmation is declined, strict=$strict manual=$manual', async ({ strict, manual }) => {
    // Given
    const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      ...(manual ? { 'app.min.js': '/* Daytona */' } : {}) });
    const before = contents(root);
    const tty = Object.getOwnPropertyDescriptor(process.stdin, 'isTTY');
    Object.defineProperty(process.stdin, 'isTTY', { configurable: true, value: true });
    const question = vi.spyOn(Interface.prototype, 'question').mockResolvedValue('n');
    const output: string[] = [];
    try {
      // When
      const code = await main([root, '--force', '--json', ...(strict ? ['--strict'] : [])],
        { out: text => output.push(text), err: () => {} });
      // Then
      expect(code).toBe(strict && manual ? 1 : 0);
      const report: Report = JSON.parse(output.join(''));
      expect(report.dryRun).toBe(true);
      expect(contents(root)).toEqual(before);
    } finally {
      question.mockRestore();
      if (tty) Object.defineProperty(process.stdin, 'isTTY', tty);
      else Reflect.deleteProperty(process.stdin, 'isTTY');
    }
  });

it('prints project-relative file headers when --print is selected', async () => {
  // Given
  const root = temporaryProject({ 'nested/index.ts': "import { Sandbox } from '@daytonaio/sdk';" });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--print']);
  // Then
  expect(result.output).toContain('--- nested/index.ts\n');
  expect(result.output).not.toContain(root);
  expect(contents(root)).toEqual(before);
});

it.each(['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'overrides', 'resolutions'].flatMap(section =>
  ['@daytonaio/sdk', '@daytona/sdk'].map(target => ({ section, target }))))(
  'reports an npm alias without rewriting it when $section targets $target', async ({ section, target }) => {
    // Given
    const manifest = JSON.stringify({ [section]: { 'daytona-sdk': `npm:${target}@^0.224.0` } });
    const root = temporaryProject({ 'package.json': manifest });
    // When
    const result = await invoke(root, ['--json', '--strict']);
    // Then
    expect(result.code).toBe(1);
    const report: Report = JSON.parse(result.output);
    expect(report.items).toContainEqual(expect.objectContaining({ file: 'package.json', kind: 'manual-dependency' }));
    expect(contents(root)['package.json']).toBe(manifest);
    expect(report.dependencies).toEqual([]);
  });

it.each(['npm:@vendor/sdk@^0.224.0', 'npm:@daytonaio/sdk-other@^0.224.0'])(
  'does not report an unrelated npm alias when its spec is %s', async spec => {
    // Given
    const root = temporaryProject({ 'package.json': JSON.stringify({ dependencies: { alias: spec, '@daytonaio/sdk': '0.224.0' } }) });
    // When
    const result = await invoke(root, ['--json']);
    // Then
    const report: Report = JSON.parse(result.output);
    expect(report.items).toEqual([]);
  });
