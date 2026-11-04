/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, ts } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Mapping } from './model.js';
import { initializer, unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';
import { isModuleLiteral, isModuleLoader } from './module-loader.js';
import { bindingKey, propertyKey } from './binding-key.js';
import { isMemberAccess, memberName } from './member-access.js';

export function daytonaModule(value: string, mapping: Mapping): boolean {
  return mapping.v0Modules.ts.old.includes(value) || mapping.v0Modules.ts.keep.includes(value);
}
function moduleOf(node: Node): string | undefined {
  return node.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierValue();
}
export function declarations(node: Node): readonly Node[] {
  const symbol = node.getSymbol();
  return (symbol?.getAliasedSymbol() ?? symbol)?.getDeclarations() ?? [];
}
export function importEqualsModule(node: Node) {
  if (!Node.isImportEqualsDeclaration(node)) return undefined;
  const reference = node.getModuleReference();
  if (!Node.isExternalModuleReference(reference)) return undefined;
  const module = reference.getExpression();
  return isModuleLiteral(module) ? module : undefined;
}
function reexportedName(source: SourceFile, name: string, context: { readonly mapping: Mapping; readonly seen: Set<Node> }): string | undefined {
  if (context.seen.has(source)) return undefined;
  const seen = new Set(context.seen).add(source);
  for (const declaration of source.getExportDeclarations()) {
    const specifier = declaration.getNamedExports().find(entry => (entry.getAliasNode()?.getText() ?? entry.getName()) === name);
    const star = declaration.getNamedExports().length === 0 && !declaration.getNamespaceExport() && name !== 'default';
    if (!specifier && !star) continue;
    const exported = specifier?.getName() ?? name;
    const module = declaration.getModuleSpecifierValue();
    if (module && module !== '@daytona/sdk/v2' && daytonaModule(module, context.mapping)) return exported;
    const target = declaration.getModuleSpecifierSourceFile();
    const resolved = target ? reexportedName(target, exported, { ...context, seen }) : !module && specifier ?
      importedName(specifier.getNameNode(), context.mapping, new Set(seen)) : undefined;
    if (resolved) return resolved;
  }
  return undefined;
}
export function importedName(node: Node, mapping: Mapping, seen: Set<Node> = new Set()): string | undefined {
  if (seen.has(node)) return undefined;
  seen.add(node);
  if (isMemberAccess(node) || Node.isQualifiedName(node)) {
    const left = Node.isQualifiedName(node) ? node.getLeft() : node.getExpression();
    const right = Node.isQualifiedName(node) ? node.getRight().getText() : memberName(node);
    if (right === undefined) return undefined;
    if (isNamespace(left, mapping)) return right;
    for (const declaration of left.getSymbol()?.getDeclarations() ?? []) {
      if (!Node.isNamespaceImport(declaration)) continue;
      const target = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierSourceFile();
      if (target) return reexportedName(target, right, { mapping, seen });
    }
  }
  const symbol = node.getSymbol();
  for (const declaration of symbol?.getDeclarations() ?? []) {
    if (!bindingIsStable(declaration)) continue;
    if (Node.isImportSpecifier(declaration)) {
      const module = moduleOf(declaration);
      if (module && module !== '@daytona/sdk/v2' && daytonaModule(module, mapping)) return declaration.getName();
      const target = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierSourceFile();
      if (target) return reexportedName(target, declaration.getName(), { mapping, seen });
    }
    if (Node.isBindingElement(declaration)) {
      const variable = declaration.getFirstAncestorByKind(SyntaxKind.VariableDeclaration);
      const init = variable?.getInitializer();
      if (declaration.getParent() !== variable?.getNameNode()) continue;
      if (init && (isRequire(init, mapping) || isNamespace(init, mapping))) return bindingKey(declaration);
    }
  }
  return undefined;
}
export function isRequire(node: Node, mapping: Mapping): boolean {
  if (!Node.isCallExpression(node) || !isModuleLoader(node)) return false;
  const arg = node.getArguments()[0];
  return isModuleLiteral(arg) && daytonaModule(arg.getLiteralValue(), mapping);
}
export function isNamespace(node: Node, mapping: Mapping, seen: Set<Node> = new Set()): boolean {
  if (Node.isParenthesizedExpression(node) || Node.isAsExpression(node) || Node.isNonNullExpression(node) || Node.isSatisfiesExpression(node)) {
    return isNamespace(node.getExpression(), mapping, seen);
  }
  const value = unwrap(node);
  if (seen.has(value)) return false;
  seen.add(value);
  if (Node.isCallExpression(value) && (isRequire(value, mapping) ||
      Node.isAwaitExpression(node) && value.getExpression().getKind() === SyntaxKind.ImportKeyword)) {
    const module = value.getArguments()[0];
    return isModuleLiteral(module) && module.getLiteralValue() !== '@daytona/sdk/v2' && daytonaModule(module.getLiteralValue(), mapping);
  }
  for (const declaration of value.getSymbol()?.getDeclarations() ?? []) {
    if (!bindingIsStable(declaration)) continue;
    const external = importEqualsModule(declaration);
    if (external && external.getLiteralValue() !== '@daytona/sdk/v2' && daytonaModule(external.getLiteralValue(), mapping)) return true;
    if (Node.isNamespaceImport(declaration)) {
      const module = moduleOf(declaration);
      if (module && module !== '@daytona/sdk/v2' && daytonaModule(module, mapping)) return true;
    }
    const init = initializer(declaration);
    if (init && isRequire(init, mapping)) return true;
    if (init && Node.isVariableDeclaration(declaration) && Node.isIdentifier(declaration.getNameNode()) && isNamespace(init, mapping, seen)) return true;
  }
  return false;
}
export function annotatedKind(node: Node, mapping: Mapping): string | undefined {
  if (!(Node.isVariableDeclaration(node) || Node.isParameterDeclaration(node) || Node.isPropertyDeclaration(node) || Node.isPropertySignature(node))) return undefined;
  const annotation = node.getTypeNode();
  return annotation ? typeKind(annotation, mapping) : undefined;
}
export function typeKind(annotation: Node, mapping: Mapping): string | undefined {
  if (Node.isParenthesizedTypeNode(annotation)) return typeKind(annotation.getTypeNode(), mapping);
  if (Node.isUnionTypeNode(annotation)) {
    const kinds = annotation.getTypeNodes().filter(type => !['null', 'undefined'].includes(type.getText()))
      .map(type => typeKind(type, mapping));
    const first = kinds[0];
    return first && kinds.every(kind => kind === first) ? first : undefined;
  }
  if (!Node.isTypeReference(annotation)) return undefined;
  const name = importedName(annotation.getTypeName(), mapping);
  if (!name) return undefined;
  if (name === 'Daytona') return 'client';
  for (const [kind, receiver] of Object.entries(mapping.receivers)) if (receiver.ts.types.includes(name)) return kind;
  return undefined;
}
export function constructorMayReplaceInstance(node: Node): boolean {
  if (!Node.isClassDeclaration(node) && !Node.isClassExpression(node)) return false;
  return node.getConstructors().some(constructor => constructor.getDescendantsOfKind(SyntaxKind.ReturnStatement).some(returned => {
    const expression = returned.getExpression();
    return expression !== undefined && !Node.isThisExpression(unwrap(expression)) &&
      returned.getFirstAncestor(ancestor => ts.isFunctionLike(ancestor.compilerNode)) === constructor;
  }));
}
export function isClientConstructor(expression: Node, mapping: Mapping, context: {
  readonly seen: Set<Node>; readonly inheritedOnly: boolean; readonly allowReplacement?: boolean;
  readonly staticMember?: string;
} = { seen: new Set(), inheritedOnly: false }): boolean {
  const target = unwrap(expression);
  if (context.seen.has(target)) return false;
  context.seen.add(target);
  if (isMemberAccess(target) && declarations(target.getExpression()).some(declaration => !bindingIsStable(declaration))) return false;
  if (importedName(target, mapping) === 'Daytona') return true;
  const parent = target.getParent();
  const candidates = Node.isClassExpression(target) ? [target] : Node.isShorthandPropertyAssignment(parent) ?
    parent.getValueSymbol()?.getDeclarations() ?? [] : declarations(target);
  if (candidates.length !== 1) return false;
  const declaration = candidates[0];
  if (Node.isImportSpecifier(declaration)) {
    return importedName(declaration.getAliasNode() ?? declaration.getNameNode(), mapping) === 'Daytona';
  }
  if (Node.isParameterDeclaration(declaration) || Node.isVariableDeclaration(declaration)) {
    const init = declaration.getInitializer();
    if ((context.inheritedOnly || context.staticMember !== undefined) && Node.isVariableDeclaration(declaration) && init) {
      return bindingIsStable(declaration) && isClientConstructor(init, mapping, context);
    }
    const annotation = declaration.getTypeNode();
    if (bindingIsStable(declaration) && Node.isTypeQuery(annotation) &&
      isClientConstructor(annotation.getExprName(), mapping, context)) return true;
  }
  if (Node.isShorthandPropertyAssignment(declaration)) {
    return bindingIsStable(declaration) && isClientConstructor(declaration.getNameNode(), mapping, context);
  }
  if (Node.isPropertyAssignment(declaration) && bindingIsStable(declaration)) {
    const init = declaration.getInitializer();
    return init !== undefined && isClientConstructor(init, mapping, context);
  }
  if (Node.isClassDeclaration(declaration) || Node.isClassExpression(declaration)) {
    if (context.staticMember !== undefined && declaration.getStaticMembers().some(member => propertyKey(member) === context.staticMember)) return false;
    if (!context.allowReplacement && constructorMayReplaceInstance(declaration)) return false;
    if (context.inheritedOnly && declaration.getConstructors().length > 0) return false;
    const base = declaration.getExtends()?.getExpression();
    return base !== undefined && isClientConstructor(base, mapping, context);
  }
  if (!Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return false;
  const init = declaration.getInitializer();
  return init !== undefined && isClientConstructor(init, mapping, context);
}
export function inheritsClientConstructor(expression: Node, mapping: Mapping): boolean {
  return isClientConstructor(expression, mapping, { seen: new Set(), inheritedOnly: true });
}
export function isConstructor(node: Node, mapping: Mapping, allowReplacement = false): boolean {
  const value = unwrap(node);
  return Node.isNewExpression(value) && isClientConstructor(value.getExpression(), mapping, { seen: new Set(), inheritedOnly: false, allowReplacement });
}
export function hasDaytonaImport(source: Node, mapping: Mapping): boolean {
  return source.getDescendants().filter(isModuleLiteral).some(node => {
    const parent = node.getParent();
    if (Node.isModuleDeclaration(parent) && parent.getNameNode() === node) return daytonaModule(node.getLiteralValue(), mapping) ||
      mapping.v0Modules.ts.old.some(module => node.getLiteralValue().startsWith(`${module}/`));
    if (!daytonaModule(node.getLiteralValue(), mapping)) return false;
    return Node.isImportDeclaration(parent) || Node.isExportDeclaration(parent) || Node.isExternalModuleReference(parent) ||
      Node.isLiteralTypeNode(parent) && Node.isImportTypeNode(parent.getParent()) ||
      Node.isCallExpression(parent) && (parent.getExpression().getKind() === SyntaxKind.ImportKeyword ||
        isModuleLoader(parent) || ['jest.mock', 'vi.mock'].includes(parent.getExpression().getText()));
  });
}
