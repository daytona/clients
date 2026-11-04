/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';

it.each([
  `const apiUrl='timeout';const c={[apiUrl]:'https://vendor.test'};`,
  `var apiUrl='timeout';var c={[apiUrl]:'https://vendor.test'};`,
  `let apiUrl=readKey();const c={[apiUrl]:'https://vendor.test'};`,
  `const serverUrl='timeout';class C { [serverUrl]='https://vendor.test'; }`,
  `const apiUrl='timeout';const c={get [apiUrl](){return 'https://vendor.test'}};`,
  `Object.defineProperty(c,'timeout',{value:'https://vendor.test'});`,
  `Reflect.defineProperty(c,'timeout',{value:'https://vendor.test'});`,
  `const c={get apiUrl(){function other(){return 'https://vendor.test'}return '';}};`,
  `class C { apiUrl=''; serverUrl='https://app.daytona.io/api'; }`,
  `const c={get apiUrl(){return 'https://app.daytona.io/api/'}};`,
  `Object.defineProperty(c,'apiUrl',{value:'https://app.daytona.io/api'});`,
  `Reflect.defineProperty(c,'serverUrl',{value:''});`,
])('accepts generated non-URL keys and safe values when source is %s', async input => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.min.js': `/* Daytona */${input}` });
  // When
  const result = await invoke(root, ['--dry', '--json']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items.filter(item => item.kind === 'custom-api-url')).toEqual([]);
});

it.each([
  "Object.defineProperty(c,'apiUrl',{get(){return 'https://custom/api'}});",
  "Object.defineProperty(c,'apiUrl',{get:()=> 'https://custom/api'});",
  "Object.defineProperty(c,'apiUrl',{get:function(){return 'https://custom/api'}});",
])('refuses generated custom URL descriptor getters when source is %s', async input => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.min.js': `/* Daytona */${input}` });
  // When
  const result = await invoke(root, ['--dry', '--json', '--drop-api-url']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'app.min.js', kind: 'custom-api-url' }));
});

it.each([
  "Object.defineProperty(c,'apiUrl',{get(){return getUrl()}});",
  "Object.defineProperty(c,'apiUrl',{get:()=> getUrl()});",
  "Object.defineProperty(c,'apiUrl',{get:function(){return getUrl()}});",
])('reports unresolved generated descriptor getter when source is %s', async input => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.min.js': `/* Daytona */${input}` });
  // When
  const result = await invoke(root, ['--dry', '--json', '--drop-api-url']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(0);
  expect(report.items).toContainEqual(expect.objectContaining({ file: 'app.min.js', kind: 'config-field' }));
});

it.each([1, 5, 6, 300])('caps generated refusal details per file when there are %s literals', async count => {
  // Given
  const bundle = '/* Daytona */\n' + Array.from({ length: count }, (_, i) => `const c${i}={apiUrl:'https://vendor${i}.test'};`).join('\n');
  const root = temporaryProject({ 'package.json': manifest, 'a.min.js': bundle, 'b.min.js': bundle });
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json', '--drop-api-url']);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code).toBe(2);
  for (const file of ['a.min.js', 'b.min.js']) {
    const items = report.items.filter(item => item.file === file && item.kind === 'custom-api-url');
    expect(items).toHaveLength(Math.min(count, 5) + (count > 5 ? 1 : 0));
    if (count > 5) expect(items.at(-1)?.message).toBe(`${count - 5} more custom URL literals in ${file}`);
    expect(items.slice(0, 5).every(item => item.line > 0 && item.column > 0)).toBe(true);
  }
  expect(report.changedFiles).toEqual([]);
  expect(contents(root)).toEqual(before);
});

it.each(['ts', 'js', 'mjs', 'cjs'].flatMap(extension => ['window', 'self'].flatMap(name => [false, true].map(drop => ({ extension, name, drop })))))
  ('recognizes unbound $name.process.env in $extension when drop=$drop', async ({ extension, name, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest,
      [`boot.${extension}`]: `${name}.process = ${name}.process || {env:{}}; ${name}.process.env.DAYTONA_API_URL = 'https://corp';` });
    // When
    const result = await invoke(root, ['--dry', '--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    const report: Report = JSON.parse(result.output);
    expect(result.code).toBe(drop ? 0 : 2);
    expect(report.items).toContainEqual(expect.objectContaining({ kind: 'env-api-url', file: `boot.${extension}` }));
    expect(report.refused?.reason).toBe(drop ? undefined : 'custom-api-url');
  });

it.each(['delete', 'source-directory'])('proceeds past an unrelated vendor bundle when remedy is %s', async remedy => {
  // Given
  const root = temporaryProject({ 'package.json': manifest,
    'src/client.ts': "import { Daytona } from '@daytonaio/sdk'; new Daytona().create();",
    'public/vendor.min.js': "/* Daytona */const vendor={apiUrl:'https://vendor.test'};" });
  const bundle = join(root, 'public/vendor.min.js');
  if (remedy === 'delete') rmSync(bundle);
  // When
  const result = await invoke(remedy === 'delete' ? root : join(root, 'src'), ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(readFileSync(join(root, 'src/client.ts'), 'utf8')).toContain('new Daytona().v0.create()');
});
