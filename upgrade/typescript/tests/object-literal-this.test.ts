/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project, SyntaxKind } from 'ts-morph';
import { objectIdentity } from '../src/object-identity.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each([
  'register({ service: this });',
  'register({ nested: { owner: this } });',
  'route({ handler: async () => this.daytona.create() });',
  'route({ nested: { handler: () => ({ owner: this }) } });',
])('retains lexical this when an object contains %s', statement => {
  // Given an object value or arrow inside a class method.
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.ts',
    `class Service { run() { ${statement} } }`);
  const receiver = source.getFirstDescendantByKindOrThrow(SyntaxKind.ThisKeyword);
  // When
  const identity = objectIdentity(receiver);
  // Then the object literal does not replace the enclosing receiver.
  expect(identity).toBe(source.getClassOrThrow('Service'));
});

it.each([
  'method() { return this; }',
  'get owner() { return this; }',
  'set owner(value) { register(this); }',
  'method() { return () => this; }',
])('binds object this when the member is %s', member => {
  // Given an object member with its own receiver inside a class method.
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.ts',
    `class Service { run() { const object = { ${member} }; } }`);
  const receiver = source.getFirstDescendantByKindOrThrow(SyntaxKind.ThisKeyword);
  // When
  const identity = objectIdentity(receiver);
  // Then object methods/accessors, including their arrows, retain object identity.
  expect(identity).toBe(source.getFirstDescendantByKindOrThrow(SyntaxKind.ObjectLiteralExpression));
});

it('binds dynamic this when an object property contains an ordinary function', () => {
  // Given a function expression rather than a lexical arrow.
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.ts',
    'class Service { run() { route({ handler: function () { return this; } }); } }');
  const receiver = source.getFirstDescendantByKindOrThrow(SyntaxKind.ThisKeyword);
  // When
  const identity = objectIdentity(receiver);
  // Then the function binds its own receiver, not the class receiver.
  expect(identity).toBe(source.getFirstDescendantByKindOrThrow(SyntaxKind.FunctionExpression));
});

it.each([
  'register({ service: this });',
  'register({ nested: { owner: this } });',
])('requires review when the class escapes in %s', async statement => {
  // Given an initializer-only client field exposed through an object payload.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'app.ts': `import { Daytona } from '@daytonaio/sdk';
      class Service {
        daytona = new Daytona();
        run() { ${statement} return this.daytona.create(); }
      }`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then outside code can replace the holder field, so it cannot migrate silently.
  expect(result.code, result.output).toBe(1);
  expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade)');
  expect(contents(root)['app.ts']).not.toContain('this.daytona.v0.create()');
});

it('migrates the client call when an object callback only reads its SDK field', async () => {
  // Given a lexical callback that exposes no writable reference to the holder.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'app.ts': `import { Daytona } from '@daytonaio/sdk';
      class Service {
        daytona = new Daytona();
        run() { route({ handler: async () => this.daytona.create() }); }
      }`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then merely accessing the SDK field in the callback preserves its proof.
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toContain('this.daytona.v0.create()');
});
