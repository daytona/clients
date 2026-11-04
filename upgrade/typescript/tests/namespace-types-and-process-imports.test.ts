/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it('preserves await when suggesting an inline sandbox service call', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk'; const d = new Daytona(); (await d.create()).fs.listFiles('.');" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ suggestion: expect.objectContaining({ v2: expect.stringContaining('(await d.create()).fs.list(') }) })]) });
});

it('keeps namespace resource imports type-only when the owner is type-only', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import type * as sdk from '@daytonaio/sdk'; let sandbox: sdk.Sandbox;" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain("import type * as sdkV0 from '@daytona/sdk/v0'");
});

it('keeps import-equals resource imports type-only when the owner is type-only', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import type sdk = require('@daytonaio/sdk'); let sandbox: sdk.Sandbox;" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toContain("import type sdkV0 = require('@daytona/sdk/v0')");
});

it('accepts string-literal destructuring keys when present in pinned exports', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': 'const { "Image": I } = require("@daytonaio/sdk"); I.debianSlim();' });
  // When
  const result = await invoke(root, ['--json'], { ...loadMapping(), v0Exports: { ts: ['Image'] } });
  // Then
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'private-import' })]));
});

it.each(["import * as sdk from '@daytonaio/sdk';", "const sdk = require('@daytonaio/sdk');"])(
  'leaves nested namespace constructors unproven when initialized through %s', async declaration => {
    // Given
    const root = temporaryProject({ 'index.ts': `${declaration}\nconst tools = sdk; const { nested: { Daytona } } = tools; const client = new Daytona(); client.create();` });
    // When
    await invoke(root);
    // Then
    expect(contents(root)['index.ts']).not.toContain('client.v0.create()');
  },
);

it('reports manual guidance when a typed service has moved to another sandbox path', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import type { Process } from '@daytonaio/sdk'; function f(service: Process) { service.createPty(); }" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]) });
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'suggestion' })]));
});

it.each(["import process from 'node:process';", "import process from 'process';", "import * as process from 'node:process';"])(
  'refuses an imported Node process URL when declared by %s', async declaration => {
    // Given
    const root = temporaryProject({ 'client.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();", 'index.ts': `${declaration} process.env.DAYTONA_API_URL = 'https://custom/api';` });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(contents(root)).toEqual(before);
  },
);

it('ignores a local process shadow when a Node process import exists', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import process from 'node:process'; import { Daytona } from '@daytonaio/sdk'; function f(process: Local) { process.env.DAYTONA_API_URL = 'https://custom/api'; } new Daytona().create();" });
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]));
});

it.each(["import system from 'node:process';", "import { default as system } from 'node:process';"])(
  'refuses aliased Node process URLs when declared by %s', async declaration => {
    // Given
    const root = temporaryProject({ 'index.ts': `${declaration} system.env.DAYTONA_SERVER_URL = 'https://custom/api';` });
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ kind: 'custom-api-url' })]) });
  },
);
