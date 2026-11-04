/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Mapping } from './model.js';
import { bindingKey } from './binding-key.js';
import { isModuleLiteral, isModuleLoader } from './module-loader.js';
import { isMemberAccess, memberName } from './member-access.js';
import { unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';
import { isSharedError } from './v0-exports.js';

function referencesNamespace(node: Node, binding: Node, seen: Set<Node> = new Set()): boolean {
  const value = unwrap(node);
  if (seen.has(value)) return false;
  seen.add(value);
  const symbol = value.getSymbol();
  if (!symbol) return false;
  const declarations = symbol.getDeclarations();
  if (!declarations.every(bindingIsStable)) return false;
  if (symbol === binding.getSymbol()) return true;
  return declarations.some(declaration => {
    if (!Node.isVariableDeclaration(declaration) || !Node.isIdentifier(declaration.getNameNode())) return false;
    const init = declaration.getInitializer();
    return init !== undefined && referencesNamespace(init, binding, seen);
  });
}

/** Each affected import contributes guidance to both comments and the report. */
export function errorImportSuggestions(source: SourceFile, mapping: Mapping): ReadonlyMap<Node, string> {
  const guidance = new Map<Node, string>();
  const accesses = source.getDescendants().filter(node => isMemberAccess(node) || Node.isQualifiedName(node));
  const variables = source.getDescendantsOfKind(SyntaxKind.VariableDeclaration);
  for (const module of source.getDescendants().filter(isModuleLiteral)) {
    if (module.getLiteralValue() !== mapping.v0Modules.ts.v0) continue;
    const parent = module.getParent();
    let names: readonly string[] = [];
    let namespace: Node | undefined;
    if (Node.isImportDeclaration(parent)) {
      names = parent.getNamedImports().map(entry => entry.getName());
      namespace = parent.getNamespaceImport();
    } else if (Node.isExportDeclaration(parent)) {
      names = parent.getNamedExports().map(entry => entry.getName());
    } else if (Node.isExternalModuleReference(parent)) {
      const declaration = parent.getParentIfKind(SyntaxKind.ImportEqualsDeclaration);
      namespace = declaration?.getNameNode();
    } else if (Node.isCallExpression(parent) && parent.getArguments()[0] === module &&
        (isModuleLoader(parent) || parent.getExpression().getKind() === SyntaxKind.ImportKeyword)) {
      let value: Node = parent;
      while (Node.isParenthesizedExpression(value.getParent()) || Node.isAwaitExpression(value.getParent())) value = value.getParentOrThrow();
      const owner = value.getParent();
      if (Node.isVariableDeclaration(owner)) {
        const binding = owner.getNameNode();
        if (Node.isObjectBindingPattern(binding)) names = binding.getElements().flatMap(entry => bindingKey(entry) ?? []);
        else namespace = binding;
      } else if (owner && isMemberAccess(owner)) names = [memberName(owner) ?? ''];
    } else if (Node.isLiteralTypeNode(parent)) {
      const qualifier = parent.getParentIfKind(SyntaxKind.ImportType)?.getQualifier();
      if (Node.isIdentifier(qualifier)) names = [qualifier.getText()];
    }
    const usesError = namespace !== undefined && accesses.some(access => {
      const receiver = Node.isQualifiedName(access) ? access.getLeft() : access.getExpression();
      const name = Node.isQualifiedName(access) ? access.getRight().getText() : memberName(access);
      return name !== undefined && isSharedError(name, mapping) && referencesNamespace(receiver, namespace);
    });
    const destructuresError = namespace !== undefined && variables.some(variable => {
      const binding = variable.getNameNode();
      const init = variable.getInitializer();
      return Node.isObjectBindingPattern(binding) && init !== undefined &&
        binding.getElements().some(entry => !entry.getDotDotDotToken() && isSharedError(bindingKey(entry) ?? '', mapping)) &&
        referencesNamespace(init, namespace);
    });
    if (usesError || destructuresError || names.some(name => isSharedError(name, mapping))) guidance.set(module,
      'when moving these calls to v2, import shared errors from @daytona/sdk so catches also cover v2 errors; root errors take only a message and have no 0.x fields');
  }
  return guidance;
}
