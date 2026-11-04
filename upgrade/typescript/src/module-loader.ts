/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, ts } from 'ts-morph';
import type { NoSubstitutionTemplateLiteral, StringLiteral } from 'ts-morph';
import { bindingIsStable } from './stable-bindings.js';

export function isModuleLiteral(node: Node | undefined): node is StringLiteral | NoSubstitutionTemplateLiteral {
  return Node.isStringLiteral(node) || Node.isNoSubstitutionTemplateLiteral(node);
}

function isCreateRequire(node: Node): boolean {
  if (Node.isPropertyAccessExpression(node)) return node.getName() === 'createRequire' &&
    (node.getExpression().getSymbol()?.getDeclarations() ?? []).some(declaration => Node.isNamespaceImport(declaration) &&
      ['module', 'node:module'].includes(declaration.getFirstAncestorByKindOrThrow(SyntaxKind.ImportDeclaration).getModuleSpecifierValue()));
  return (node.getSymbol()?.getDeclarations() ?? []).some(declaration => Node.isImportSpecifier(declaration) &&
    declaration.getName() === 'createRequire' &&
    ['module', 'node:module'].includes(declaration.getFirstAncestorByKindOrThrow(SyntaxKind.ImportDeclaration).getModuleSpecifierValue()));
}
export function isModuleLoader(node: Node): boolean {
  if (!Node.isCallExpression(node)) return false;
  const callee = node.getExpression();
  if (!Node.isIdentifier(callee) || callee.getText() !== 'require') return false;
  const declarations = callee.getSymbol()?.getDeclarations() ?? [];
  return declarations.length === 0 || declarations.every(declaration => {
    if (declaration.getSourceFile().isDeclarationFile() && (!ts.isExternalModule(declaration.getSourceFile().compilerNode) ||
      declaration.getSourceFile().getFilePath().replaceAll('\\', '/').includes('/node_modules/@types/node/'))) return bindingIsStable(declaration);
    if (!Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return false;
    const init = declaration.getInitializer();
    return Node.isCallExpression(init) && isCreateRequire(init.getExpression());
  });
}
