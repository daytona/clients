/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { invoke, temporaryProject } from './helpers.js';

function project(source: string): string {
  return temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytonaio/sdk":"^0.231.1"}}',
    'tsconfig.json': '{"compilerOptions":{"strict":true,"module":"NodeNext","moduleResolution":"NodeNext"}}',
    'app.ts': source,
  });
}

async function suggestions(source: string): Promise<readonly { readonly id: string; readonly v2: string | null; readonly notes: readonly string[] }[]> {
  const result = await invoke(project(source), ['--suggest-v2', '--json']);
  expect(result.code, result.errors).toBe(0);
  const report: Report = JSON.parse(result.output);
  return report.items.flatMap(item => item.suggestion && !item.suggestion.id.startsWith('workflow:') ? [item.suggestion] : []);
}

it('suggests buildFromContext when a create provably passes a 0.x Dockerfile image (as the Python tool)', async () => {
  // Given 0.x creates whose image is Image.fromDockerfile(...), directly, through
  // a const, through a namespace import and through the v0 module.
  const found = await suggestions(`import { Daytona, Image } from '@daytonaio/sdk';
import * as sdk from '@daytonaio/sdk';
const client = new Daytona();
await client.snapshot.create({ name: 'web', image: Image.fromDockerfile('Dockerfile') });
const image = sdk.Image.fromDockerfile('app/Dockerfile');
await client.create({ image });
`);
  // When / Then both get the build-context form and its notes.
  expect(found.map(({ id, v2 }) => [id, v2])).toEqual([
    ['client.snapshot.create', 'client.v2.images.buildFromContext(context, { dockerfile, name, region })'],
    ['client.create', 'client.v2.images.buildFromContext(context, { dockerfile, region })'],
  ]);
  for (const suggestion of found) expect(suggestion.notes.join(' ')).toContain('Image.fromDockerfile(path) uploaded the files its COPY and ADD lines name');
});

it.each([
  ['another image builder', "await client.snapshot.create({ name: 'web', image: Image.debianSlim('3.12') });"],
  ['a spread that may replace the image', "await client.snapshot.create({ image: Image.fromDockerfile('Dockerfile'), ...rest });"],
  ['a rebound binding', "let image = Image.fromDockerfile('Dockerfile'); image = other; await client.create({ image });"],
  ['an Image of another package', "await client.snapshot.create({ name: 'web', image: Other.fromDockerfile('Dockerfile') });"],
])('keeps the registry-image form for %s', async (_name, call) => {
  // Given a create whose image is not provably a 0.x Dockerfile image.
  const found = await suggestions(`import { Daytona, Image } from '@daytonaio/sdk';
import { Image as Other } from './images.js';
declare const rest: { image: string };
declare const other: Image;
const client = new Daytona();
${call}
`);
  // Then its row keeps the ordinary form.
  expect(found.map(({ v2 }) => v2)).not.toContainEqual(expect.stringContaining('buildFromContext'));
  expect(found.length).toBe(1);
});

it('proves the Dockerfile image after the default pass moved it to the v0 module', async () => {
  // Given upgraded source: the root client and the v0 Image class.
  const found = await suggestions(`import { Daytona } from '@daytona/sdk';
import { Image as V0Image } from '@daytona/sdk/v0';
const client = new Daytona();
await client.v0.snapshot.create({ name: 'web', image: V0Image.fromDockerfile('Dockerfile') });
`);
  // Then the build-context form is suggested.
  expect(found.map(({ v2 }) => v2)).toEqual(['client.v2.images.buildFromContext(context, { dockerfile, name, region })']);
});

it('notes on a filesystem call that v2 paths are absolute, in the Python tool\'s words', async () => {
  // Given a 0.x filesystem call on a proven sandbox.
  const found = await suggestions(`import { Daytona } from '@daytonaio/sdk';
const client = new Daytona();
const sandbox = await client.create();
await sandbox.fs.listFiles('workspace');
`);
  // Then its suggestion carries the shared absolute-path note.
  const listing = found.find(({ id }) => id === 'sandbox.fs.listFiles');
  expect(listing?.notes).toContain('v2 takes only absolute sandbox paths (starting with /) and refuses a relative one before any request; v0 resolved a relative path against the sandbox working directory');
});
