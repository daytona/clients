/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { HolderEscapes } from '../src/holder-escapes.js';
import { loadMapping } from '../src/mapping.js';
import { PropertyProofs } from '../src/property-proofs.js';

it.each([false, true])('drops holder proofs when a later import resolves an escaping override (reuse=%s)', reuse => {
  // Given a cached non-escaping helper and an override not yet in the project.
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/base.ts', `
    export class Base {
      client = {};
      helper() {}
      run() { this.helper(); return this.client; }
    }
  `);
  project.getFileSystem().writeFileSync('/late.ts', `
    import { Base } from './base';
    export class Derived extends Base { helper() { replace(this); } }
  `);
  const member = source.getFirstDescendantByKindOrThrow(SyntaxKind.ReturnStatement).getExpressionOrThrow();
  const proofs = new PropertyProofs(loadMapping());
  expect(proofs.lost(member)).toBe(false);
  const loader = project.createSourceFile('/loader.ts', "import './late';");
  // When module resolution adds the escaping subclass after the first index.
  const resolved = loader.getImportDeclarations()[0]?.getModuleSpecifierSourceFile();
  expect(resolved?.getFilePath()).toBe('/late.ts');
  project.resolveSourceFileDependencies();
  const lost = (reuse ? proofs : new PropertyProofs(loadMapping())).lost(member);
  // Then both per-project and per-analyzer caches observe the new escape.
  expect(lost).toBe(true);
});

it('rebuilds the holder index when source membership changes without changing its size', () => {
  // Given a cached project and an unindexed replacement source on its filesystem.
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/holder.ts', 'const holder = {}; holder.client;');
  const unused = project.createSourceFile('/unused.ts', 'const unrelated = 1;');
  project.getFileSystem().writeFileSync('/escape.ts', 'replace(holder);');
  const escapes = new HolderEscapes();
  const initial = escapes.index(source);
  expect([...initial.values()].flat()).toHaveLength(0);
  project.removeSourceFile(unused);
  project.addSourceFileAtPath('/escape.ts');
  // When
  const effects = [...escapes.index(source).values()].flat();
  // Then the replacement source contributes its holder escape.
  expect(effects.map(effect => effect.getText())).toContain('replace(holder)');
});
