/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const manifest = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' };

it.each([
  'for (const entry of [1, 2]) { await this.client.create(); replace(this); }',
  'while (again()) { await this.client.create(); replace(this); }',
  'do { await this.client.create(); replace(this); } while (again());',
  'for (let i = 0; i < 2; i++) { await this.client.create(); replace(this); }',
  'for (const key in { a: 1, b: 2 }) { await this.client.create(); replace(this); }',
  '[1, 2].forEach(async () => { await this.client.create(); replace(this); });',
  '[1, 2].map(async () => { await this.client.create(); replace(this); });',
])('requires review when a holder escapes on a loop back-edge: %s', async body => {
  // Given an initializer-only field used before a holder escape on each iteration.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service {
    client = new Daytona();
    async run() { ${body} }
  }` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the potentially replaced field is not silently migrated.
  expect(result.code, result.output).toBe(1);
  expect(contents(root)['app.ts']).not.toContain('this.client.v0.create()');
  expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade)');
});

it('rewrites the field when a straight-line holder escape follows the use', async () => {
  // Given no loop back-edge between the escape and the use.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service {
    client = new Daytona();
    async run() { await this.client.create(); replace(this); }
  }` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the earlier use retains its proof.
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toContain('this.client.v0.create()');
});

it.each([
  { name: 'escaping helper', methods: 'reconfigure() { replace(this); }', escapes: true },
  { name: 'transitive helper', methods: 'reconfigure() { this.swap(); } swap() { replace(this); }', escapes: true },
  { name: 'non-escaping helper', methods: 'reconfigure() { return {}; }', escapes: false },
  { name: 'non-escaping transitive helper', methods: 'reconfigure() { return this.params(); } params() { return {}; }', escapes: false },
  { name: 'unresolved base method', base: 'extends MissingBase', methods: '', escapes: true },
  { name: 'dynamic method', methods: 'reconfigure() { this[name](); }', escapes: true },
  { name: 'recursive escaping helper', methods: 'reconfigure() { this.swap(); replace(this); } swap() { this.reconfigure(); }', escapes: true },
  { name: 'recursive pure helper', methods: 'reconfigure() { if (again()) this.swap(); } swap() { this.reconfigure(); }', escapes: false },
  { name: 'arrow helper', methods: 'reconfigure = () => { replace(this); };', escapes: true },
  { name: 'depth-limited helper', methods: Array.from({ length: 10 }, (_, i) =>
    `${i === 0 ? 'reconfigure' : `step${i}`}() { this.step${i + 1}(); }`).join('\n') + 'step10() {}', escapes: true },
])('handles $name when summarizing this calls', async ({ methods, escapes, base = '' }) => {
  // Given a helper invoked before the initializer-only field is used.
  const root = temporaryProject({ ...manifest, 'app.ts': prefix + `class Service ${base} {
    client = new Daytona();
    ${methods}
    run() { this.reconfigure(); return this.client.create(); }
  }` });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then only helpers proven not to expose this preserve the field proof.
  expect(result.code, result.output).toBe(escapes ? 1 : 0);
  expect(contents(root)['app.ts']?.includes('this.client.v0.create()')).toBe(!escapes);
  expect(contents(root)['app.ts']?.includes('FIXME(daytona-upgrade)')).toBe(escapes);
});

it.each([false, true])('resolves project base helpers when escaping=%s', async escapes => {
  // Given an inherited helper whose implementation is in another project file.
  const root = temporaryProject({ ...manifest,
    'base.ts': `export class Base { reconfigure() { ${escapes ? 'replace(this);' : 'return {};'} } }`,
    'app.ts': prefix + `import { Base } from './base';
      class Service extends Base {
        client = new Daytona();
        run() { this.reconfigure(); return this.client.create(); }
      }`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the inherited body controls the call-site escape.
  expect(result.code, result.output).toBe(escapes ? 1 : 0);
  expect(contents(root)['app.ts']?.includes('this.client.v0.create()')).toBe(!escapes);
});
