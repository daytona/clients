/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { resolve, join, dirname } from 'node:path';
import { createRequire } from 'node:module';

// Match the root scanner's compiler dependency, owned by the SDK.
const require = createRequire(new URL('../../sdk-typescript/package.json', import.meta.url));
const ts = require('typescript');

export function tsExports(files, path, visited = new Set()) {
  if (visited.has(path)) return new Set();
  visited.add(path);
  const file = files.find((source) => source.path === path);
  if (!file) return new Set();
  const source = ts.createSourceFile(path, file.text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const names = new Set();
  function bindingNames(binding) {
    if (ts.isIdentifier(binding)) names.add(binding.text);
    else for (const element of binding.elements) {
      if (ts.isBindingElement(element)) bindingNames(element.name);
    }
  }
  for (const statement of source.statements) {
    if (ts.isExportDeclaration(statement)) {
      const clause = statement.exportClause;
      if (clause) {
        if (ts.isNamedExports(clause)) for (const element of clause.elements) names.add(element.name.text);
        else names.add(clause.name.text);
      } else if (statement.moduleSpecifier && ts.isStringLiteral(statement.moduleSpecifier) && statement.moduleSpecifier.text.startsWith('.')) {
        const base = resolve(dirname(path), statement.moduleSpecifier.text.replace(/\.js$/, ''));
        const target = [base, `${base}.ts`, join(base, 'index.ts')].find((candidate) => files.some((source) => source.path === candidate));
        if (target) for (const name of tsExports(files, target, visited)) names.add(name);
      }
      continue;
    }
    const modifiers = ts.canHaveModifiers(statement) ? ts.getModifiers(statement) ?? [] : [];
    if (!modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword)) continue;
    if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) bindingNames(declaration.name);
    } else if ((ts.isClassDeclaration(statement) || ts.isInterfaceDeclaration(statement)
      || ts.isTypeAliasDeclaration(statement) || ts.isEnumDeclaration(statement)
      || ts.isFunctionDeclaration(statement)) && statement.name) names.add(statement.name.text);
  }
  return names;
}
