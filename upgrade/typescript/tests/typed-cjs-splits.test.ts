/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { applyEdits } from '../src/edits.js';
import { imports } from '../src/imports.js';
import { loadMapping } from '../src/mapping.js';

it.each(['const', 'let', 'var'].flatMap(keyword => ['@daytonaio/sdk', '@daytona/sdk'].map(module => ({ keyword, module }))))
('preserves module query types when splitting a $keyword CommonJS import from $module', ({ keyword, module }) => {
  // Given a type annotation that is exactly the original module's value-side exports.
  const input = `${keyword} { Daytona: D, Image: I }: typeof import('${module}') = require('${module}');`;
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('app.ts', input);
  // When
  const result = imports(source, loadMapping(), '/');
  const output = applyEdits(input, result.edits);
  // Then both declarations keep a type query for their own module.
  const migrated = project.createSourceFile('migrated.ts', output);
  expect(migrated.getVariableDeclarations().map(variable => variable.getTypeNode()?.getText()))
    .toEqual(["typeof import('@daytona/sdk')", "typeof import('@daytona/sdk/v0')"]);
  expect(result.items).toEqual([]);
});

it('retains resource member type checking when require returns an untyped value', () => {
  // Given Node-style untyped require results and distinct root/v0 export surfaces.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { strict: true, lib: ['lib.es2022.d.ts'] } });
  project.createSourceFile('globals.d.ts', `declare const require: typeof JSON.parse;
declare module '@daytona/sdk' { export class Daytona { readonly client: true; } }
declare module '@daytona/sdk/v0' { export class Image { static fromDockerfile(path: string): Image; } }`);
  const input = "const { Daytona, Image }: typeof import('@daytonaio/sdk') = require('@daytonaio/sdk');";
  const source = project.createSourceFile('app.ts', input);
  // When
  const output = applyEdits(input, imports(source, loadMapping(), '/').edits);
  source.replaceWithText(output + '\nImage.missing();');
  // Then moved bindings are not any, so an invalid resource method is still rejected.
  const resource = source.getDescendantsOfKind(SyntaxKind.BindingElement).find(binding => binding.getName() === 'Image');
  expect(resource?.getNameNode().getType().isAny()).toBe(false);
  expect(project.getPreEmitDiagnostics().map(diagnostic => diagnostic.getCode())).toEqual([2339]);
});

it.each([
  'LegacyExports',
  "Pick<typeof import('@daytonaio/sdk'), 'Daytona' | 'Image'>",
  "typeof import('@daytonaio/sdk').exports",
  "typeof import('other-sdk')",
  "typeof import('@daytonaio/sdk') & ExtraExports",
])('leaves a mixed declaration unsplit for review when its annotation is %s', annotation => {
  // Given an annotation whose meaning cannot be partitioned into root and v0 exports.
  const input = `const { Daytona, Image }: ${annotation} = require('@daytonaio/sdk');`;
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('app.ts', input);
  // When
  const result = imports(source, loadMapping(), '/');
  const output = applyEdits(input, result.edits);
  // Then no binding loses its declared type and the split is explicitly a manual task.
  const migrated = project.createSourceFile('migrated.ts', output);
  expect(migrated.getVariableDeclarations()).toHaveLength(1);
  expect(migrated.getVariableDeclarations()[0]?.getName()).toBe('{ Daytona, Image }');
  expect(migrated.getVariableDeclarations()[0]?.getTypeNode()).toBeDefined();
  expect(result.items).toContainEqual(expect.objectContaining({ kind: 'private-import' }));
});
