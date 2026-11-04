/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join } from 'node:path';
import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { objectIdentity } from '../src/object-identity.js';
import { invoke, temporaryProject } from './helpers.js';

const services = `interface Legacy { snapshot: { get(name: string): Promise<string> }; }
`;
const mappedTypes = `${services}
export declare class Daytona {}
export interface Daytona extends Record<'v0', Legacy> {}
`;
const manifest = '{"name":"@daytona/sdk","version":"2.0.0","types":"index.d.ts"}';
const config = '{"compilerOptions":{"strict":true,"module":"NodeNext","moduleResolution":"NodeNext"}}';

it.each(['v0', 'v2'])('suggests namespace services when the project is under %s', async directory => {
  // Given a real SDK-shaped getter in a package below a version-named project ancestor.
  const parent = temporaryProject({
    [`${directory}/app/package.json`]: '{"type":"module","dependencies":{"@daytona/sdk":"^2.0.0"}}',
    [`${directory}/app/tsconfig.json`]: config,
    [`${directory}/app/node_modules/@daytona/sdk/package.json`]: manifest,
    [`${directory}/app/node_modules/@daytona/sdk/index.d.ts`]: `${services}
export declare class Daytona { get v0(): Legacy; }`,
    [`${directory}/app/app.ts`]: `import { Daytona } from '@daytona/sdk';
const client = new Daytona(); await client.v0.snapshot.get('base');`,
  });
  // When
  const result = await invoke(join(parent, directory, 'app'), ['--suggest-v2', '--json']);
  // Then the ancestor's name cannot erase the SDK namespace proof.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.errors).toBe(0);
  expect(report.items.flatMap(item => item.suggestion ? [item.suggestion.id] : []))
    .toEqual(['client.snapshot.get']);
});

it('shares receiver identity when v0 symbols have no declarations', () => {
  // Given a compiler-synthesized property symbol from a mapped namespace type.
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/app.ts', `${mappedTypes}
const client = new Daytona(); unknown(client.v0); client.v0.snapshot.get('base');`);
  const accesses = source.getDescendantsOfKind(SyntaxKind.PropertyAccessExpression)
    .filter(node => node.getName() === 'v0');
  expect(accesses).toHaveLength(2);
  for (const access of accesses) {
    expect(access.getSymbol()).toBeDefined();
    expect(access.getSymbol()?.getDeclarations()).toEqual([]);
  }
  // When separate reads of the unresolved namespace are identified.
  const identities = accesses.map(access => objectIdentity(access));
  // Then each read belongs to the same client, not its own syntax node.
  expect(identities[0]).toBe(identities[1]);
});

it.each([false, true])('preserves namespace escape effects when declaration-free symbols escape: %s', async escaped => {
  // Given installed types with a declaration-free namespace and an optional external escape.
  const root = temporaryProject({
    'package.json': '{"type":"module","dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'tsconfig.json': config,
    'node_modules/@daytona/sdk/package.json': manifest,
    'node_modules/@daytona/sdk/index.d.ts': mappedTypes,
    'app.ts': `import { Daytona } from '@daytona/sdk'; const client = new Daytona();
${escaped ? 'unknown(client.v0);' : ''} await client.v0.snapshot.get('base');`,
  });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then only an unescaped client retains a proven service suggestion.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.errors).toBe(0);
  expect(report.items.some(item => item.suggestion?.id === 'client.snapshot.get')).toBe(!escaped);
});
