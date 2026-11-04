/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const manifest = '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}';
const custom = '`https://daytona.internal.corp/api`';

it.each([
  `function t(){let e={};return e.apiUrl=${custom},e.apiKey='k',e}const n=new e(t());`,
  `const c={}; c.apiUrl=${custom}; new Unknown(c);`,
  `const c={}; c['serverUrl']=${custom};`,
  `const c={}; c.apiUrl ||= ${custom};`,
  `const c={}; c.apiUrl &&= ${custom};`,
  `const c={}; c.apiUrl ??= ${custom};`,
  `var DaytonaSdk=function(){var o={apiUrl:${custom}};function Daytona(c){this.o=o}return Daytona}();`,
  `exports.x = {'apiUrl':${custom}};`,
  `var vendor={apiUrl:'https://api.stripe.com/v1',apiKey:'k'};new Stripe(vendor);`,
  `var vendor={serverUrl:'/api/v1',target:'es2015'};`,
  `const t=new e(new class{apiUrl=${custom};apiKey='k'});`,
  `class C { serverUrl = 'https://corp'; }`,
  `const c={get apiUrl(){return ${custom}}};`,
  `class C { get serverUrl(){if (flag) return ${custom}; return ''; } }`,
  `Object.defineProperty(c,'apiUrl',{value:${custom}});`,
  `Reflect.defineProperty(c,'serverUrl',{value:${custom}});`,
  `const c={};c['api'+'Url']=${custom};`,
  'const c={};c[`server${"Url"}`]=' + custom + ';',
  `const c={['api'+'Url']:${custom}};`,
  'const c={[`server${"Url"}`]:' + custom + '};',
  `const key='apiUrl';const c={[key]:${custom}};`,
  `const key='serverUrl';const c={};c[key]=${custom};`,
  `class C { ['api'+'Url']=${custom}; }`,
  `const c={get ['api'+'Url'](){return ${custom}}};`,
  `Object.defineProperty(c,'api'+'Url',{value:${custom}});`,
].flatMap(input => [false, true].map(drop => ({ input, drop }))))(
  'refuses every generated custom URL literal when drop=$drop: $input', async ({ input, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'public/app.min.js': `/* DAYTONA */${input}` });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--dry', '--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    const report: Report = JSON.parse(result.output);
    expect(result.code).toBe(2);
    expect(report.items).toContainEqual(expect.objectContaining({ file: 'public/app.min.js', kind: 'custom-api-url',
      message: 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.' }));
    expect(contents(root)).toEqual(before);
  });

it.each([
  `new Daytona({apiUrl:${custom}});`,
  `const c={}; c.apiUrl=${custom}; new Daytona(c);`,
  `const c={}; c['serverUrl'] ||= ${custom}; const alias=c; new Daytona(alias);`,
  `function make(){const c={};c.apiUrl=${custom};return c}new Daytona(make());`,
  `const c: DaytonaConfig = {}; c.apiUrl=${custom};`,
  `const c={apiUrl:${custom}}; const typed: DaytonaConfig=c;`,
].flatMap(input => [false, true].map(drop => ({ input, drop }))))(
  'always refuses a proven generated custom URL when drop=$drop: $input', async ({ input, drop }) => {
    // Given
    const root = temporaryProject({ 'package.json': manifest, 'public/app.min.ts': input });
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ changedFiles: [], refused: { reason: 'custom-api-url',
      message: 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x. No files were written.' } });
    expect(contents(root)).toEqual(before);
  });

it.each(['', 'https://app.daytona.io/api', 'https://app.daytona.io/api/'].flatMap(url => [
  `new Daytona({apiUrl:${JSON.stringify(url)}});`,
  `/* Daytona */ const c={}; c.serverUrl=\`${url}\`;`,
].flatMap(input => [false, true].map(drop => ({ input, drop })))))('accepts Cloud and empty generated literals when drop=$drop: $input', async ({ input, drop }) => {
  // Given
  const root = temporaryProject({ 'package.json': manifest, 'app.min.js': input });
  // When
  const result = await invoke(root, ['--dry', '--json', ...(drop ? ['--drop-api-url'] : [])]);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).not.toContainEqual(expect.objectContaining({ kind: 'custom-api-url' }));
});
