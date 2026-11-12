/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createRequire } from 'node:module';

// The SDK owns the compiler dependency; run npm ci in sdk-typescript/ first.
const require = createRequire(new URL('../../sdk-typescript/package.json', import.meta.url));
const ts = require('typescript');

export function collectRootTsExports(text) {
  const source = ts.createSourceFile('index.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const names = new Set();
  const problems = [];
  const unsupported = (statement) => problems.push(`ts: unsupported export form in the 2.x root (sdk-typescript/src/index.ts): ${statement.getText(source).trim()}; re-export by name`);
  // Parse errors must never turn malformed exports into an incomplete name set.
  if (source.parseDiagnostics.length > 0) {
    unsupported(source);
    return { names, problems };
  }
  function bindingNames(binding) {
    if (ts.isIdentifier(binding)) names.add(binding.text);
    else for (const element of binding.elements) {
      if (ts.isBindingElement(element)) bindingNames(element.name);
    }
  }
  for (const statement of source.statements) {
    if (ts.isExportDeclaration(statement)) {
      const clause = statement.exportClause;
      if (!clause || !ts.isNamedExports(clause) || clause.elements.some((element) => element.name.text === 'default')) unsupported(statement);
      else for (const element of clause.elements) names.add(element.name.text);
      continue;
    }
    if (ts.isExportAssignment(statement) || ts.isNamespaceExportDeclaration(statement)) {
      unsupported(statement);
      continue;
    }
    const modifiers = ts.canHaveModifiers(statement) ? ts.getModifiers(statement) ?? [] : [];
    if (!modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword)) continue;
    if (modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.DefaultKeyword)) {
      unsupported(statement);
      continue;
    }
    if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) bindingNames(declaration.name);
      continue;
    }
    // Compiler node guards form an explicit allow-list; every other exported
    // declaration (including namespace/module/import-equals) fails closed.
    if (ts.isClassDeclaration(statement) || ts.isInterfaceDeclaration(statement)
      || ts.isTypeAliasDeclaration(statement) || ts.isEnumDeclaration(statement)
      || ts.isFunctionDeclaration(statement)) {
      if (statement.name) names.add(statement.name.text);
      else unsupported(statement);
    } else unsupported(statement);
  }
  return { names, problems };
}
