/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['Object.assign', "Object['assign']"])('keeps mutated clients unproven when %s is shadowed', async assign => {
  // Given a custom assign implementation which replaces an existing client.
  const root = temporaryProject({
    'index.js': `import { Daytona } from '@daytonaio/sdk';
const Object = { assign(target, source) { target.client = source.client; } };
const holder = { client: new Daytona() };
${assign}(holder, { client: { create() { return 42; } } });
holder.client.create();`,
  });
  // When migrating without resolved SDK types.
  const result = await invoke(root, ['--json']);
  // Then lack of intrinsic identity cannot strengthen a mutated receiver proof.
  expect(contents(root)['index.js']).not.toContain('holder.client.v0.create()');
  expect(JSON.parse(result.output)).toMatchObject({ items: expect.arrayContaining([
    expect.objectContaining({ kind: 'fixme' }),
  ]) });
});
