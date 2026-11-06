/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { configs } from '../src/config.js';
import { applyEdits } from '../src/edits.js';
import { imports } from '../src/imports.js';
import { loadMapping } from '../src/mapping.js';

const mapping = loadMapping();

it.each(Object.entries(mapping.config.ts).filter(([, action]) => action === 'fixme').map(([name]) => name))(
  'retains unsupported %s while directing its removal from the 2.x config', name => {
    // Given
    const input = `import { Daytona } from '@daytonaio/sdk'; new Daytona({ ${name}: true });`;
    const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts', input);
    // When
    const result = configs(source, mapping, { root: '/', dropApiUrl: false });
    // Then
    expect(applyEdits(input, result.edits)).toBe(input);
    expect(result.items).toMatchObject([{ kind: 'config-field', message: expect.stringContaining(`Remove DaytonaConfig.${name}`) }]);
    expect(result.items[0]?.message).toContain('SDK 2.x does not accept');
  },
);

it.each(['jest.mock', 'vi.mock', 'jest.doMock', 'vi.doMock'])('requests a separate v0 mock when routing %s', helper => {
  // Given
  const input = `${helper}('@daytonaio/sdk', () => ({ Daytona: class {}, Sandbox: class {} }));`;
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts', input);
  // When
  const result = imports(source, mapping, '/');
  // Then
  expect(applyEdits(input, result.edits)).toContain(`${helper}('@daytona/sdk',`);
  expect(result.items).toMatchObject([{ kind: 'mock-target', message: expect.stringContaining('@daytona/sdk/v0 needs its own mock') }]);
});

it.each([
  "import { MissingResource } from '@daytonaio/sdk';",
  "export { MissingResource } from '@daytonaio/sdk';",
  "const { MissingResource } = require('@daytonaio/sdk');",
  "import * as sdk from '@daytonaio/sdk'; sdk.MissingResource;",
])('reports an unavailable bundled resource with actionable alternatives for %s', input => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts', input);
  // When
  const result = imports(source, { ...mapping, v0Exports: { ts: [] } }, '/');
  // Then
  expect(result.items).toMatchObject([{ kind: 'private-import', message: expect.stringContaining(`MissingResource is not exported by bundled v${mapping.v0Version}`) }]);
  expect(result.items[0]?.message).toContain('use the v2 API or remove it');
  expect(result.items[0]?.message).toContain('unavailable from @daytona/sdk/v0');
});
