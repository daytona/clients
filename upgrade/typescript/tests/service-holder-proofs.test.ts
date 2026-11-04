/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project, SyntaxKind } from 'ts-morph';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const manifest = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' };

it.each([false, true].flatMap(types => ['', 'this.params();'].map(helper => ({ types, helper }))))
('rewrites service fields when types=$types and helper=$helper', async ({ types, helper }) => {
  // Given a service, with and without its internal helper call.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service {
    private readonly daytona: Daytona = new Daytona();
    params() { return {}; }
    a() { ${helper} this.daytona.create(); this.daytona.delete(undefined); }
    c() { this.daytona.list(); }
  }
  new Service();` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then all three client calls migrate, including the independent method.
  expect(result.code, result.output).toBe(types ? 0 : 1);
  expect(contents(root)['app.ts']?.match(/this\.daytona\.v0\./g)).toHaveLength(3);
});

it.each([false, true].flatMap(types => ['readonly client: Daytona', 'readonly client', 'client: Daytona', 'client'].map(field => ({ types, field }))))
('uses field evidence after an external escape when types=$types and field=$field', async ({ types, field }) => {
  // Given explicit readonly evidence, checker-backed annotation, or just an initializer.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service {
    ${field} = new Daytona();
    run() { mutate(this); this.client.create(); }
  }` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only proof-grade field types survive an external holder escape.
  const proven = field.startsWith('readonly') || types && field.includes(': Daytona');
  expect(result.code, result.output).toBe(proven && types ? 0 : 1);
  expect(contents(root)['app.ts']?.includes('this.client.v0.create()')).toBe(proven);
});

it.each([false, true].flatMap(types => ['', 'this.params();'].map(helper => ({ types, helper }))))
('keeps plain fields across internal methods when types=$types and helper=$helper', async ({ types, helper }) => {
  // Given an initializer-only field and a class-local method call.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service {
    client = new Daytona();
    params() { return {}; }
    run() { ${helper} this.client.create(); }
  }` });
  if (types) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then calling the helper is not an escape.
  expect(result.code, result.output).toBe(types ? 0 : 1);
  expect(contents(root)['app.ts']).toContain('this.client.v0.create()');
});

it.each(['mutate(this);', 'console.log(this);', 'new Service();'])
('limits this effects to later uses in the same function: %s', async effect => {
  // Given a plain field with calls before/after the effect and in another method.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service {
    client = new Daytona();
    run() { this.client.create(); ${effect} this.client.create(); }
    other() { this.client.list(); }
  }` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only a genuinely escaping this fences the later use; construction is independent.
  expect(result.code, result.output).toBe(effect === 'new Service();' ? 0 : 1);
  const output = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.ts', contents(root)['app.ts'] ?? '');
  const migrated = output.getDescendantsOfKind(SyntaxKind.CallExpression)
    .filter(call => call.getExpression().getText().startsWith('this.client.v0.'));
  expect(migrated).toHaveLength(effect === 'new Service();' ? 3 : 2);
});
