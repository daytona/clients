/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Mapping } from './model.js';
import { daytonaModule, importEqualsModule } from './bindings.js';

export function importStyle(source: SourceFile, mapping: Mapping): {
  readonly quote: string; readonly semicolon: string; readonly newline: string;
} {
  const imports = [
    ...source.getImportDeclarations().map(declaration => ({ declaration, module: declaration.getModuleSpecifier() })),
    ...source.getDescendantsOfKind(SyntaxKind.ImportEqualsDeclaration).map(declaration => ({ declaration, module: importEqualsModule(declaration) })),
  ];
  const daytona = imports.filter(entry => entry.module && daytonaModule(entry.module.getLiteralValue(), mapping));
  const candidates = daytona.length ? daytona : imports.length ? imports : source.getExportDeclarations().map(declaration => ({ declaration, module: declaration.getModuleSpecifier() }));
  const semicolons = candidates.filter(entry => entry.declaration.getText().endsWith(';')).length;
  const doubleQuotes = candidates.filter(entry => entry.module?.getText().startsWith('"')).length;
  const majority = candidates.length / 2;
  const first = candidates[0];
  return {
    semicolon: semicolons > majority || semicolons === majority && (first?.declaration.getText().endsWith(';') ?? true) ? ';' : '',
    quote: doubleQuotes > majority || doubleQuotes === majority && first?.module?.getText().startsWith('"') ? '"' : "'",
    newline: source.getFullText().includes('\r\n') ? '\r\n' : '\n',
  };
}
