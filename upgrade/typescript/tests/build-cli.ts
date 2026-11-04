/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Vitest global setup: build dist/ once, before any test file spawns dist/cli.js.
// Test files run in parallel, so none of them may rebuild it.
import { execFileSync } from 'node:child_process';
import { copyFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export default function setup(): void {
  const compiler = fileURLToPath(new URL('../node_modules/typescript/bin/tsc', import.meta.url));
  execFileSync(process.execPath, [compiler], { cwd: fileURLToPath(new URL('..', import.meta.url)), stdio: 'inherit' });
  copyFileSync(new URL('../src/mapping.json', import.meta.url), new URL('../dist/mapping.json', import.meta.url));
}
