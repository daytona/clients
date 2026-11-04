/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { chmodSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const source = "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();\n";
// Windows chmod does not deny reads, and root reads through any mode.
const permissionTest = it.skipIf(process.platform === 'win32' || process.getuid?.() === 0);

permissionTest.each([
  'blocked.ts', 'blocked.js', 'other/package.json', '.env', 'compose.yaml',
  'Dockerfile', 'deploy/app.yaml', 'component.vue', 'tsconfig.json', 'jsconfig.json',
])('reports unreadable %s without writing readable files', async file => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.ts': source,
    [file]: file.endsWith('.json') ? '{}' : '// blocked\n' });
  const before = contents(root);
  const path = join(root, file);
  const mode = statSync(path).mode;
  chmodSync(path, 0);
  try {
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(1);
    expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], items: expect.arrayContaining([
      { file, line: 0, column: 0, kind: 'unsupported-file',
        message: `${file} could not be read: Permission denied; fix its permissions, then rerun.` },
    ]) });
    expect(result.errors).toBe('');
  } finally { chmodSync(path, mode); }
  expect(contents(root)).toEqual(before);
});

permissionTest.each([{ flags: [] }, { flags: ['--dry'] }, { flags: ['--print'] }])(
  'accumulates unreadable files and directories with $flags', async ({ flags }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'main.ts': source,
      'blocked.ts': source, '.env': 'DAYTONA_API_KEY=secret\n',
      'nested/private/main.ts': source, 'other/private/.env': 'DAYTONA_API_KEY=secret\n' });
    const before = contents(root);
    const files = ['blocked.ts', '.env', 'nested/private', 'other/private'];
    const permissions = files.map(file => ({ file, mode: statSync(join(root, file)).mode }));
    for (const { file } of permissions) chmodSync(join(root, file), 0);
    try {
      // When
      const result = await invoke(root, ['--json', ...flags]);
      // Then
      expect(result.code).toBe(1);
      const report = JSON.parse(result.output);
      expect(report.changedFiles).toEqual([]);
      expect(report.items.filter((item: { readonly kind: string }) => item.kind === 'unsupported-file'))
        .toEqual(expect.arrayContaining(files.map(file => ({ file, line: 0, column: 0, kind: 'unsupported-file',
          message: `${file} could not be read: Permission denied; fix its permissions, then rerun.` }))));
      expect(report.items.filter((item: { readonly kind: string }) => item.kind === 'unsupported-file')).toHaveLength(4);
    } finally { for (const { file, mode } of permissions) chmodSync(join(root, file), mode); }
    expect(contents(root)).toEqual(before);
  },
);

permissionTest.each([
  { file: 'custom.ts', text: "import { Daytona } from '@daytonaio/sdk'; new Daytona({apiUrl:'https://private.example'});\n" },
  { file: '.env', text: 'DAYTONA_API_URL=https://private.example\n' },
])('keeps readable custom URL precedence in $file', async ({ file, text }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.ts': source,
    'blocked.ts': source, 'private/main.ts': source, [file]: text });
  const before = contents(root);
  chmodSync(join(root, 'blocked.ts'), 0);
  chmodSync(join(root, 'private'), 0);
  try {
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' },
      items: expect.arrayContaining(['blocked.ts', 'private'].map(file => ({ file, line: 0, column: 0,
        kind: 'unsupported-file', message: `${file} could not be read: Permission denied; fix its permissions, then rerun.` }))) });
  } finally {
    chmodSync(join(root, 'blocked.ts'), 0o600);
    chmodSync(join(root, 'private'), 0o700);
  }
  expect(contents(root)).toEqual(before);
});

permissionTest('does not read unrelated binary files or ignored directories', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.ts': source,
    'image.bin': '\0binary', 'node_modules/private/data.bin': '\0binary', '.env.example': 'example' });
  const paths = ['image.bin', 'node_modules/private', '.env.example'];
  for (const file of paths) chmodSync(join(root, file), 0);
  try {
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(JSON.parse(result.output).changedFiles).toEqual(['main.ts', 'package.json']);
  } finally { for (const file of paths) chmodSync(join(root, file), file.endsWith('private') ? 0o700 : 0o600); }
});

permissionTest.each([
  { 'tsconfig.json': '{"include":["**/*.ts"]}', 'base.json': '{}' },
  { 'tsconfig.json': '{"extends":"./base.json"}', 'base.json': '{"compilerOptions":{"strict":true}}' },
])('reports compiler-loaded unreadable files while preserving custom URL precedence', async config => {
  // Given
  const root = temporaryProject({ ...config, 'package.json': manifest, 'blocked.ts': source,
    'private/main.ts': source, 'custom.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona({apiUrl:'https://private.example'});\n" });
  const before = contents(root);
  const files = ['blocked.ts', 'base.json', 'private'];
  const permissions = files.map(file => ({ file, mode: statSync(join(root, file)).mode }));
  for (const { file } of permissions) chmodSync(join(root, file), 0);
  try {
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    const report = JSON.parse(result.output);
    expect(report).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url' } });
    const expected = config['tsconfig.json'].includes('extends') ? files : ['blocked.ts', 'private'];
    expect(report.items.filter((item: { readonly kind: string }) => item.kind === 'unsupported-file'))
      .toEqual(expect.arrayContaining(expected.map(file => ({ file, line: 0, column: 0, kind: 'unsupported-file',
        message: `${file} could not be read: Permission denied; fix its permissions, then rerun.` }))));
  } finally { for (const { file, mode } of permissions) chmodSync(join(root, file), mode); }
  expect(contents(root)).toEqual(before);
});

permissionTest('accumulates unreadable scan inputs even when a source has a parse error', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.ts': source, 'broken.ts': 'const broken = ;',
    '.env': 'DAYTONA_API_KEY=secret', 'other/package.json': '{}' });
  const before = contents(root);
  const files = ['.env', 'other/package.json'];
  for (const file of files) chmodSync(join(root, file), 0);
  try {
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(1);
    expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], items: expect.arrayContaining(files.map(file => ({
      file, line: 0, column: 0, kind: 'unsupported-file',
      message: `${file} could not be read: Permission denied; fix its permissions, then rerun.`,
    }))) });
  } finally { for (const file of files) chmodSync(join(root, file), 0o600); }
  expect(contents(root)).toEqual(before);
});

permissionTest('reports an unreadable project root', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.ts': source });
  const before = contents(root);
  const mode = statSync(root).mode;
  chmodSync(root, 0);
  try {
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(1);
    expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], items: [
      { file: '.', line: 0, column: 0, kind: 'unsupported-file',
        message: '. could not be read: Permission denied; fix its permissions, then rerun.' },
    ] });
  } finally { chmodSync(root, mode); }
  expect(contents(root)).toEqual(before);
});

permissionTest('reports an unreadable project package.json once', async () => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'main.ts': source });
  chmodSync(join(root, 'package.json'), 0);
  try {
    // When
    const result = await invoke(root, ['--json']);
    // Then it is an unsupported file, not also an ancestor-manifest item.
    expect(result.code).toBe(1);
    const items: readonly { readonly file: string; readonly kind: string }[] = JSON.parse(result.output).items;
    expect(items.filter(item => item.file === 'package.json').map(item => item.kind)).toEqual(['unsupported-file']);
  } finally {
    chmodSync(join(root, 'package.json'), 0o600);
  }
});

it('keeps the missing-dependency item when package.json is a directory', async () => {
  // Given a directory where the manifest would be, and no other manifest.
  const root = temporaryProject({ 'package.json/keep': '', 'main.ts': source });
  // When
  const result = await invoke(root, ['--json', '--dry']);
  // Then the missing dependency is still reported.
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'manual-dependency' }));
});
