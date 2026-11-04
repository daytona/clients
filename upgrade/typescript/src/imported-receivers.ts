/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { importEqualsModule } from './bindings.js';
import { bindingKey } from './binding-key.js';
import { initializer, unwrap } from './edits.js';
import { isModuleLiteral, isModuleLoader } from './module-loader.js';

export type ImportedReceiver = {
  readonly source: SourceFile; readonly module: string; readonly name: string; readonly property?: string;
};

export function importedReceiver(node: Node, seen: ReadonlySet<Node> = new Set()): ImportedReceiver | undefined {
  const value = unwrap(node, true);
  if (!Node.isIdentifier(value) || seen.has(value) || seen.size >= 8) return undefined;
  for (const declaration of value.getSymbol()?.getDeclarations() ?? []) {
    const source = declaration.getSourceFile();
    const imported = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration);
    if (imported) {
      const name = Node.isImportSpecifier(declaration) ? declaration.getName() : Node.isNamespaceImport(declaration) ? '*' : 'default';
      return { source, module: imported.getModuleSpecifierValue(), name };
    }
    const external = importEqualsModule(declaration);
    if (external) return { source, module: external.getLiteralValue(), name: 'default' };
    const variable = Node.isVariableDeclaration(declaration) ? declaration : Node.isBindingElement(declaration) &&
      Node.isVariableDeclaration(declaration.getParent().getParent()) ? declaration.getParent().getParent() : undefined;
    const init = variable ? initializer(variable) : undefined;
    if (!init) continue;
    const key = Node.isBindingElement(declaration) ? bindingKey(declaration) : undefined;
    if (Node.isCallExpression(init) && isModuleLoader(init)) {
      const module = init.getArguments()[0];
      if (isModuleLiteral(module)) return { source, module: module.getLiteralValue(), name: key ?? 'default' };
    }
    // Only one destructuring level directly from an imported binding, never a nested service.
    if (key !== undefined) {
      const parent = importedReceiver(init, new Set(seen).add(value));
      if (parent && parent.property === undefined) return { ...parent, property: key };
    }
  }
  return undefined;
}
