/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const manifest = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' };

it.each([
  { name: 'escaping base', base: 'helper() { replace(this); }', escapes: true },
  { name: 'clean base', base: 'helper() { return {}; }', escapes: false },
  { name: 'unresolved base', base: '', escapes: true },
  { name: 'transitive base escape', base: 'helper() { this.swap(); } swap() { replace(this); }', escapes: true },
])('follows super calls when the helper has an $name', async ({ base, escapes }) => {
  // Given a wrapper invoking a base helper on the same instance.
  const root = temporaryProject({ ...manifest,
    'base.ts': `export class Base { ${base} }`,
    'app.ts': prefix + `import { Base } from './base';
      class Service extends Base {
        client = new Daytona();
        configure() { super.helper(); }
        run() { this.configure(); return this.client.create(); }
      }`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only a resolved non-escaping base implementation preserves the proof.
  expect(result.code, result.output).toBe(escapes ? 1 : 0);
  expect(contents(root)['app.ts']?.includes('this.client.v0.create()')).toBe(!escapes);
  expect(contents(root)['app.ts']?.includes('FIXME(daytona-upgrade)')).toBe(escapes);
});

it('requires review when a direct super call escapes before the client use', async () => {
  // Given no intermediate this helper to carry the super effect.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `
    class Base { helper() { replace(this); } }
    class Service extends Base {
      client = new Daytona();
      run() { super.helper(); return this.client.create(); }
    }` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the derived instance's later field use is fenced.
  expect(result.code, result.output).toBe(1);
  expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade)');
  expect(contents(root)['app.ts']).not.toContain('this.client.v0.create()');
});

it.each([
  { name: 'direct method', subclass: 'class Child extends Service { helper() { replace(this); } }', escapes: true },
  { name: 'transitive method', subclass: 'class Mid extends Service {} class Child extends Mid { helper() { replace(this); } }', escapes: true },
  { name: 'arrow field', subclass: 'class Child extends Service { helper = () => { replace(this); }; }', escapes: true },
  { name: 'class expression', subclass: 'const Child = class extends Service { helper() { replace(this); } };', escapes: true },
  { name: 'opaque field', subclass: 'class Child extends Service { helper = external; }', escapes: true },
  { name: 'accessor override', subclass: 'class Child extends Service { get helper() { return () => replace(this); } }', escapes: true },
  { name: 'computed method', subclass: 'class Child extends Service { [key]() { replace(this); } }', escapes: true },
  { name: 'opaque mixin hierarchy', subclass: 'class Child extends mixin(Service) { helper() { replace(this); } }', escapes: true },
  { name: 'clean override', subclass: 'class Child extends Service { helper() { return {}; } }', escapes: false },
  { name: 'inherited clean helper', subclass: 'class Child extends Service {}', escapes: false },
])('includes subclass dispatch when there is a $name', async ({ subclass, escapes }) => {
  // Given a clean base helper whose receiver can be a project subclass.
  const root = temporaryProject({ ...manifest,
    'app.ts': prefix + `export class Service {
      client = new Daytona();
      helper() { return {}; }
      run() { this.helper(); return this.client.create(); }
    }`,
    'child.ts': `import { Service } from './app'; ${subclass}`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then a possible escaping or unresolved override cannot certify the base call.
  expect(result.code, result.output).toBe(escapes ? 1 : 0);
  expect(contents(root)['app.ts']?.includes('this.client.v0.create()')).toBe(!escapes);
  expect(contents(root)['app.ts']?.includes('FIXME(daytona-upgrade)')).toBe(escapes);
});

it('keeps super and virtual dispatch summaries separate when they share a base body', async () => {
  // Given a clean super call followed by virtual dispatch to a possible escaping override.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `
    class Base { helper() {} }
    class Service extends Base {
      client = new Daytona();
      run() { super.helper(); this.helper(); return this.client.create(); }
    }
    class Child extends Service { helper() { replace(this); } }` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then caching the base body's non-escape cannot certify the virtual call.
  expect(result.code, result.output).toBe(1);
  expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade)');
  expect(contents(root)['app.ts']).not.toContain('this.client.v0.create()');
});

it.each([
  { name: 'declaration', nested: 'function callback() { replace(this); } callback();', escapes: false },
  { name: 'expression', nested: 'const callback = function () { replace(this); }; callback();', escapes: false },
  { name: 'dynamic this alias', nested: 'function callback() { const other = this; replace(other); } callback();', escapes: false },
  { name: 'nested ordinary helper call', nested: 'function callback() { this.unknown(); } callback();', escapes: false },
  { name: 'object method', nested: 'const other = { callback() { replace(this); } }; other.callback();', escapes: false },
  { name: 'arrow', nested: 'const callback = () => { replace(this); }; callback();', escapes: true },
  { name: 'arrow inside ordinary function', nested: 'function callback() { (() => replace(this))(); } callback();', escapes: false },
  { name: 'owner-bound callback', nested: '(function () { replace(this); }).call(this);', escapes: true },
  { name: 'captured owner alias', nested: 'const owner = this; function callback() { replace(owner); } callback();', escapes: true },
])('distinguishes the owner from nested this when using a $name', async ({ nested, escapes }) => {
  // Given a helper with either dynamic or lexical this in a nested function.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service {
    client = new Daytona();
    helper() { ${nested} }
    run() { this.helper(); return this.client.create(); }
  }` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only exposure of the method's own receiver loses its field proof.
  expect(result.code, result.output).toBe(escapes ? 1 : 0);
  expect(contents(root)['app.ts']?.includes('this.client.v0.create()')).toBe(!escapes);
  expect(contents(root)['app.ts']?.includes('FIXME(daytona-upgrade)')).toBe(escapes);
});
