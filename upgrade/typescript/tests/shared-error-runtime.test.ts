/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, symlinkSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { expect, it } from 'vitest';
import { invoke, temporaryProject } from './helpers.js';
import { loadMapping } from '../src/mapping.js';

// Like SDK bundle smoke tests, these run only against an explicitly supplied,
// freshly built and bundled SDK (a stale local typescript/dist would
// test old code): DAYTONA_UPGRADE_TEST_SDK=<path to a built typescript/>.
const sdk = process.env['DAYTONA_UPGRADE_TEST_SDK'] ? resolve(process.env['DAYTONA_UPGRADE_TEST_SDK']) : '';
const bundled = sdk !== '' && existsSync(join(sdk, 'dist/cjs/v0/index.js'));

it.skipIf(!bundled).each([
  {
    name: 'an escaping message-only instance',
    files: { 'raise.mjs': `import { DaytonaNotFoundError } from '@daytonaio/sdk';
export function raise() { throw new DaytonaNotFoundError('missing'); }` },
    status: undefined,
  },
  {
    name: 'a class expression forwarding 0.x arguments',
    files: { 'raise.mjs': `import { DaytonaNotFoundError } from '@daytonaio/sdk';
const Custom = class extends DaytonaNotFoundError { constructor(message, status) { super(message, status); } };
export function raise() { throw new Custom('missing', 404); }` },
    status: 404,
  },
  {
    name: 'a re-exported 0.x constructor',
    files: {
      'errors.mjs': "export { DaytonaNotFoundError as Missing } from '@daytonaio/sdk';",
      'raise.mjs': `import { Missing } from './errors.mjs';
export function raise() { throw new Missing('missing', 404); }`,
    },
    status: 404,
  },
])('catches routed v0 errors with root markers when using $name', async ({ files, status }) => {
  // Given: a real built SDK, not replacement error classes.
  const root = temporaryProject({ ...files, 'index.mjs': `
import { DaytonaError, DaytonaNotFoundError } from ${JSON.stringify(pathToFileURL(join(sdk, 'dist/esm/index.js')).href)};
import { raise } from './raise.mjs';
try { raise(); } catch (error) {
  if (!(error instanceof DaytonaNotFoundError) || !(error instanceof DaytonaError)) throw error;
  console.log(JSON.stringify({ message: error.message, status: error.statusCode }));
}
` });
  mkdirSync(join(root, 'node_modules/@daytona'), { recursive: true });
  symlinkSync(sdk, join(root, 'node_modules/@daytona/sdk'), 'dir');
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  const output = execFileSync(process.execPath, [join(root, 'index.mjs')], { encoding: 'utf8' });
  expect(JSON.parse(output)).toEqual({ message: 'missing', ...(status === undefined ? {} : { status }) });
});

it.skipIf(!bundled).each(loadMapping().rootNames.ts.filter(name => /^Daytona.*Error$/.test(name)))(
  'preserves 0.x fields and root instanceof when migrating %s', async name => {
    // Given: every error is constructed by the real bundled SDK.
    const root = temporaryProject({ 'index.mjs': `
import { ${name} } from '@daytonaio/sdk';
import { ${name} as RootError, DaytonaError as RootBase } from ${JSON.stringify(pathToFileURL(join(sdk, 'dist/esm/index.js')).href)};
const error = new ${name}('missing', 404, { detail: 'example' }, 'FUTURE_ERROR');
console.log(JSON.stringify({ caught: error instanceof RootError && error instanceof RootBase,
  message: error.message, status: error.statusCode, headers: error.headers, code: error.errorCode }));
` });
    mkdirSync(join(root, 'node_modules/@daytona'), { recursive: true });
    symlinkSync(sdk, join(root, 'node_modules/@daytona/sdk'), 'dir');
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(readFileSync(join(root, 'index.mjs'), 'utf8')).toContain(`import { ${name} } from '@daytona/sdk/v0'`);
    const output = execFileSync(process.execPath, [join(root, 'index.mjs')], { encoding: 'utf8' });
    expect(JSON.parse(output)).toEqual({ caught: true, message: 'missing', status: 404,
      headers: { detail: 'example' }, code: 'FUTURE_ERROR' });
  },
);
