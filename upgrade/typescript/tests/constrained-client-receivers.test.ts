/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { Project } from 'ts-morph';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

function sdkFiles(major: number): Readonly<Record<string, string>> {
  const module = major === 0 ? '@daytonaio/sdk' : '@daytona/sdk';
  return {
    'package.json': JSON.stringify({ dependencies: { [module]: `${major}.0.0` } }),
    'tsconfig.json': '{"compilerOptions":{"strict":true,"skipLibCheck":true,"noEmit":true}}',
    [`node_modules/${module}/package.json`]: JSON.stringify({ name: module, version: `${major}.0.0`, types: 'index.d.ts' }),
    [`node_modules/${module}/index.d.ts`]: `export declare class Daytona {
      v0: { create(): void };
      close(): void;
      ${major === 0 ? 'create(): void;' : ''}
    }`,
  };
}

it.each([0, 2].flatMap(major => [
  '(client: Daytona) => client.create()',
  '<T extends Daytona>(client: T) => client.create()',
  '<T extends Daytona, U extends T>(client: U) => client.create()',
].map(program => ({ major, program })) ))('migrates $program like a plain root parameter with SDK $major installed', async ({ major, program }) => {
  // Given: installed declarations distinguish a root client from a bundled v0 client.
  const module = major === 0 ? '@daytonaio/sdk' : '@daytona/sdk';
  const root = temporaryProject({ ...sdkFiles(major), 'app.ts': `import { Daytona } from '${module}';\nconst run = ${program};\n` });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.output).toBe(0);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(contents(root)['app.ts']).toContain('client.v0.create()');
});

it('repairs a generic FIXME after replacing installed 0.x declarations with 2.x', async () => {
  // Given: the source left by the previous migration, before the suggested install.
  const root = temporaryProject({ ...sdkFiles(0),
    'app.ts': "import { Daytona } from '@daytona/sdk';\n// FIXME(daytona-upgrade): review the root call\nconst run = <T extends Daytona>(client: T) => client.create();\n" });
  for (const [name, text] of Object.entries(sdkFiles(2))) {
    const path = join(root, name);
    mkdirSync(join(path, '..'), { recursive: true });
    writeFileSync(path, text);
  }
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then: strict cannot go green while leaving a nonexistent root call behind.
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toContain('client.v0.create()');
  const project = new Project({ tsConfigFilePath: join(root, 'tsconfig.json') });
  expect(project.getPreEmitDiagnostics()).toEqual([]);
});

it.each([
  'class Cache { create() {} } const run = <T extends Cache>(client: T) => client.create();',
  'const run = <T extends Daytona["v0"]>(client: T) => client.create();',
])('leaves non-root constraints untouched when %s', async program => {
  // Given
  const source = `import { Daytona } from '@daytona/sdk';\n${program}\n`;
  const root = temporaryProject({ ...sdkFiles(2), 'app.ts': source });
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toBe(source);
});
