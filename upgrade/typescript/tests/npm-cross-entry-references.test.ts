/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['@daytonaio/sdk', '@daytona/sdk'].flatMap(name => [false, true].map(nested => ({ name, nested }))))(
  'reports cross-entry npm references to $name with nested=$nested', async ({ name, nested }) => {
    // Given
    const reference = { other: `$${name}` };
    const overrides = nested ? { parent: reference } : reference;
    const root = temporaryProject({ 'package.json': JSON.stringify({ dependencies: { [name]: '0.224.0' }, overrides }) });
    // When
    const result = await invoke(root, ['--json', '--strict']);
    // Then
    expect(result.code).toBe(1);
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'config-field', file: 'package.json' }));
    expect(JSON.parse(contents(root)['package.json'] ?? '')).toMatchObject({ dependencies: { '@daytona/sdk': '^2.0.0' }, overrides });
  },
);

it('keeps unrelated npm reference overrides clean', async () => {
  // Given
  const root = temporaryProject({ 'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"},"overrides":{"foo":"$bar"}}' });
  // When
  const result = await invoke(root, ['--json', '--strict']);
  // Then
  expect(result.code).toBe(0);
  expect(JSON.parse(result.output).items).toEqual([]);
});
