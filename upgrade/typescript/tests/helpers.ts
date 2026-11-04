/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach } from 'vitest';
import { main } from '../src/cli.js';
import { walk } from '../src/discovery.js';
import type { Mapping } from '../src/model.js';

export const here = fileURLToPath(new URL('.', import.meta.url));
const temporary: string[] = [];
afterEach(() => { for (const root of temporary.splice(0)) rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 }); });
export function temporaryProject(files: Readonly<Record<string, string>> = {}): string {
  const root = mkdtempSync(join(tmpdir(), 'daytona-upgrade-'));
  temporary.push(root);
  for (const [file, text] of Object.entries(files)) {
    const destination = join(root, file);
    mkdirSync(join(destination, '..'), { recursive: true });
    writeFileSync(destination, text);
  }
  return root;
}
export function fixture(name: string): string {
  const root = temporaryProject();
  cpSync(join(here, 'fixtures', name, 'input'), root, { recursive: true });
  if (name === 'type-checker' || name === 'cross-module-client') installFakeSdk(root);
  return root;
}
export function installFakeSdk(root: string): void {
  const destination = join(root, 'node_modules/@daytonaio/sdk');
  mkdirSync(destination, { recursive: true });
  cpSync(join(here, 'fake-sdk'), destination, { recursive: true });
}
export function contents(root: string): Record<string, string> {
  return Object.fromEntries(walk(root).map(file => [relative(root, file), readFileSync(file, 'utf8')]));
}
export async function invoke(root: string, flags: readonly string[] = [], mapping?: Mapping): Promise<{ readonly code: number; readonly output: string; readonly errors: string }> {
  const output: string[] = []; const errors: string[] = [];
  const code = await main([root, '--force', '--yes', ...flags],
    { out: text => output.push(text), err: text => errors.push(text) }, mapping);
  return { code, output: output.join(''), errors: errors.join('') };
}
