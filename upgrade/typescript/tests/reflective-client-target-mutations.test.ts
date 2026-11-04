/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';
import { ts } from 'ts-morph';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const setup = "const local = {create(){return 'local-cache'}}; const service = {client: new Daytona()};\n";
const modes = ['js', 'ts'].flatMap(extension => [false, true].map(types => ({ extension, types })));
const mutations = [
  "Reflect.set(target, 'client', local);",
  "Reflect.defineProperty(target, 'client', {value: local});",
  "Object.defineProperty(target, 'client', {value: local});",
  'Object.defineProperties(target, {client: {value: local}});',
  'Object.assign(target, {client: local});',
] as const;
const targets = [
  'const alias = service; let target = (alias); WRITE',
  'function replace(target TYPE) { WRITE } replace(service);',
  'const target = (() => service)(); WRITE',
  "const target = {service}['service']; WRITE",
  'let target = {}; target = service; WRITE',
] as const;

it.each(modes.flatMap(mode => mutations.flatMap(write => targets.map(target => ({ ...mode, write, target })))))
('preserves replaced receivers in $extension with types=$types: $target $write', async ({ extension, types, write, target }) => {
  // Given: widened parameters and opaque expressions can refer to the same object.
  const program = target.replace('TYPE', extension === 'ts' ? ': object' : '').replace('WRITE', write);
  const file = `app.${extension}`;
  const root = temporaryProject({ [file]: prefix + setup + program + '\nservice.client.create();' });
  if (types) installFakeSdk(root);
  class Daytona {}
  // When
  const result = await invoke(root, ['--json']);
  // Then: migration must preserve the local replacement's observable behavior.
  expect(result.code).toBe(0);
  const after = contents(root)[file] ?? '';
  expect(after).not.toContain('service.client.v0.create()');
  expect(JSON.parse(result.output).items).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'fixme' })]));
  const js = ts.transpileModule(after, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  expect(runInNewContext(js, { require: () => ({ Daytona }), exports: {} })).toBe('local-cache');
});

const widenedAliases = [
  'const target: object = service;',
  'let target: Record<string, unknown> = service;',
  'const target = service as any;',
  'const target = service as unknown;',
  'const alias: object = service; const target = (alias as unknown)!;',
  'const alias: object = service; const target = ((alias) satisfies object);',
] as const;

it.each([false, true].flatMap(types => widenedAliases.flatMap(alias => mutations.map(write => ({ types, alias, write })))))
('traces widened TypeScript aliases with types=$types: $alias $write', async ({ types, alias, write }) => {
  // Given
  const root = temporaryProject({ 'app.ts': prefix + setup + alias + write + 'service.client.create();' });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the calls stay unrewritten and are reported for manual review.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(contents(root)['app.ts']).not.toContain('service.client.v0.create()');
});

const uncertainKeys = [
  'Reflect.set(target, key, local);',
  'Reflect.defineProperty(target, key, {value: local});',
  'Object.defineProperty(target, key, {value: local});',
  'Object.defineProperties(target, {[key]: {value: local}});',
  'Object.assign(target, {[key]: local});',
  'Object.assign(target, replacement);',
  'Object.defineProperties(target, descriptors);',
] as const;

it.each(modes.flatMap(mode => uncertainKeys.map(write => ({ ...mode, write }))))
('invalidates unknown keys on opaque targets in $extension with types=$types: $write', async ({ extension, types, write }) => {
  // Given
  const root = temporaryProject({ [`app.${extension}`]: prefix + setup +
    `const second = {client: new Daytona()}; function replace(target${extension === 'ts' ? ': unknown' : ''}, key, replacement, descriptors) { ${write} } service.client.create(); second.client.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the calls stay unrewritten and are reported for manual review.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(contents(root)[`app.${extension}`]).not.toContain('service.client.v0.create()');
  expect(contents(root)[`app.${extension}`]).not.toContain('second.client.v0.create()');
});

it.each([false, true].flatMap(types => ['any', 'Record<string, unknown>'].flatMap(annotation =>
  mutations.map(write => ({ types, annotation, write })))))
('invalidates widened parameters with types=$types: $annotation $write', async ({ types, annotation, write }) => {
  // Given
  const root = temporaryProject({ 'app.ts': prefix + setup +
    `function replace(target: ${annotation}) { ${write} } service.client.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then the calls stay unrewritten and are reported for manual review.
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  expect(contents(root)['app.ts']).not.toContain('service.client.v0.create()');
});

it.each(modes.flatMap(mode => mutations.map(write => ({ ...mode, write }))))
('keeps a different resolved object proven in $extension with types=$types: $write', async ({ extension, types, write }) => {
  // Given: the alias hides its type but refers to a distinct object.
  const root = temporaryProject({ [`app.${extension}`]: prefix + setup +
    `const other = {client: new Daytona()}; const alias${extension === 'ts' ? ': object' : ''} = other; const target = (alias); ${write} service.client.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root);
  // Then the proof survives.
  expect(result.code).toBe(0);
  expect(contents(root)[`app.${extension}`]).toContain('service.client.v0.create()');
});

it.each(modes.flatMap(mode => mutations.map(write => ({ ...mode, write: write.replaceAll('client', 'other') }))))
('keeps unrelated property names proven in $extension with types=$types: $write', async ({ extension, types, write }) => {
  // Given
  const root = temporaryProject({ [`app.${extension}`]: prefix + setup +
    `function replace(target${extension === 'ts' ? ': object' : ''}) { ${write} } service.client.create();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root);
  // Then the proof survives.
  expect(result.code).toBe(0);
  expect(contents(root)[`app.${extension}`]).toContain('service.client.v0.create()');
});
