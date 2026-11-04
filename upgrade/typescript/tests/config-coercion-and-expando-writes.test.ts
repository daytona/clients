/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";

it.each(['ts', 'js'].flatMap(extension => ['', "Object.assign(cfg, { apiKey: 'key' });"].map(setup =>
  ({ extension, setup }))))('permits builtin String observation in $extension: $setup', async ({ extension, setup }) => {
  // Given an ordinary config observed by the genuine String builtin.
  const file = `index.${extension}`;
  const root = temporaryProject({ [file]: prefix + `const cfg = {}; ${setup} String(cfg); new Daytona(cfg).create();` });
  // When migrating the project.
  const result = await invoke(root, ['--json']);
  // Then the observation does not prevent migration.
  expect(result.code).toBe(0);
  expect(contents(root)[file]).toContain('new Daytona(cfg).v0.create()');
});

it.each(['ts', 'js'].flatMap(extension => [
  'const String = configure;',
  'function run(String) { String(cfg); }',
  'cfg.toString = configure;',
  'cfg[Symbol.toPrimitive] = configure;',
  'Object.assign(cfg, { toString: configure });',
  'const alias = cfg; Object.assign(alias, { valueOf: configure });',
  'cfg.__proto__ = { toString: configure };',
].map(setup => ({ extension, setup }))))('refuses unsafe String observation: $extension $setup', async ({ extension, setup }) => {
  // Given a shadowed builtin or a user-controlled conversion hook.
  const root = temporaryProject({ [`index.${extension}`]: prefix +
    `const cfg = {}; ${setup} String(cfg); new Daytona(cfg).create();` });
  const before = contents(root);
  // When migrating without asserting an unknown URL is Cloud.
  const result = await invoke(root, ['--json']);
  // Then unknown user code cannot silently publish the config to Cloud.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['toString', 'valueOf', '[Symbol.toPrimitive]'])('refuses a config conversion hook %s', async hook => {
  // Given a conversion hook that can mutate its receiver through unknown code.
  const root = temporaryProject({ 'index.ts': prefix +
    `const cfg = { ${hook}() { configure(this); return ''; } }; String(cfg); new Daytona(cfg).create();` });
  const before = contents(root);
  // When migrating the project.
  const result = await invoke(root, ['--json']);
  // Then executing conversion hooks is not treated as read-only observation.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['undefined', 'null', "'https://app.daytona.io/api'", "'https://app.daytona.io/api/'"].flatMap(value =>
  ['apiUrl', 'serverUrl', '["apiUrl"]'].map(field => ({ value, field }))))('drops a JS default or Cloud assignment: $field = $value', async ({ value, field }) => {
  // Given JS expando declarations on a const config binding.
  const access = field.startsWith('[') ? `cfg${field}` : `cfg.${field}`;
  const root = temporaryProject({ 'index.js': prefix + `const cfg = {}; ${access} = ${value}; new Daytona(cfg).create();` });
  // When migrating the project.
  const result = await invoke(root, ['--json']);
  // Then the obsolete URL assignment is removed and the receiver is migrated.
  expect(result.code).toBe(0);
  expect(contents(root)['index.js']).not.toContain(access);
  expect(contents(root)['index.js']).toContain('new Daytona(cfg).v0.create()');
});

it.each([false, true].flatMap(drop => [
  "const cfg = {}; cfg.apiUrl = 'https://custom.example';",
  "const cfg = {}; cfg.apiUrl = 'https://app.daytona.io/api'; cfg.apiUrl = 'https://custom.example';",
].map(setup => ({ drop, setup }))))('refuses known custom JS URL with drop=$drop: $setup', async ({ drop, setup }) => {
  // Given a known custom assignment, including a later override of a Cloud URL.
  const root = temporaryProject({ 'index.js': prefix + `${setup} new Daytona(cfg).create();` });
  const before = contents(root);
  // When migrating, even with an unknown-URL assertion.
  const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
  // Then known custom URLs still refuse atomically.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});

it.each(['ts', 'js'].flatMap(extension => ['let', 'const'].map(kind => ({ extension, kind }))))('refuses config binding reassignment: $extension $kind', async ({ extension, kind }) => {
  // Given an initializer that is superseded by an unknown config.
  const root = temporaryProject({ [`index.${extension}`]: prefix +
    `${kind} cfg = {}; cfg.apiUrl = undefined; cfg = configure(); new Daytona(cfg).create();` });
  const before = contents(root);
  // When migrating the project.
  const result = await invoke(root, ['--json']);
  // Then property declaration repair does not certify reassigned bindings.
  expect(result.code).toBe(2);
  expect(contents(root)).toEqual(before);
});
