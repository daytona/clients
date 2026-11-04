/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const manifest = { 'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}' };
const prefix = "import { Daytona } from '@daytonaio/sdk';\n";

it.each([
  { installed: false, field: 'private daytona = new Daytona();' },
  { installed: true, field: 'private daytona = new Daytona();' },
  { installed: false, field: 'private daytona: Daytona = new Daytona();' },
  { installed: false, field: 'private daytona!: Daytona;' },
])('migrates unrelated holders when Error is extended ($field, installed: $installed)', async ({ installed, field }) => {
  // Given an unrelated ambient base and an ordinary helper on a holder.
  const root = temporaryProject({ ...manifest,
    'errors.ts': 'export class AppError extends Error {}',
    'app.ts': prefix + `class Service {
      ${field}
      label() { return 'service'; }
      run() { this.label(); return this.daytona.create(); }
    }`,
  });
  if (installed) installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then the unrelated base cannot invalidate this holder's helper proof.
  expect(result.code, result.output).toBe(installed ? 0 : 1);
  expect(contents(root)['app.ts']).toContain('this.daytona.v0.create()');
  expect(contents(root)['app.ts']).not.toContain('FIXME(daytona-upgrade)');
});

it.each([
  { name: 'owner', base: '', helper: "label() { return 'service'; }" },
  { name: 'project base', base: "label() { return 'base'; }", helper: '' },
])('resolves helpers on the $name before reaching an ambient Error base', async ({ base, helper }) => {
  // Given a resolved project chain ending at an ambient interface/constructor.
  const root = temporaryProject({ ...manifest,
    'base.ts': `export class Base extends Error { ${base} }`,
    'app.ts': prefix + `import { Base } from './base';
      class Service extends Base {
        private daytona = new Daytona();
        ${helper}
        run() { this.label(); return this.daytona.create(); }
      }`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then lookup stops at the project implementation, not the ambient ancestor.
  expect(result.code, result.output).toBe(0);
  expect(contents(root)['app.ts']).toContain('this.daytona.v0.create()');
});

it('requires review when the helper is declared only by an opaque base', async () => {
  // Given a library helper with no analyzable body that could expose its receiver.
  const root = temporaryProject({ ...manifest,
    'opaque.d.ts': 'export declare const Opaque: new () => { label(): void };',
    'app.ts': prefix + `import { Opaque } from './opaque';
      class Service extends Opaque {
        private daytona = new Daytona();
        run() { this.label(); return this.daytona.create(); }
      }`,
  });
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--strict', '--json']);
  // Then an unresolved helper keeps the original call for manual review.
  expect(result.code, result.output).toBe(1);
  expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade)');
  expect(contents(root)['app.ts']).not.toContain('this.daytona.v0.create()');
});
