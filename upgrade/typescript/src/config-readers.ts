/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { declarations, inheritsClientConstructor, isClientConstructor } from './bindings.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import type { Mapping } from './model.js';
import { isConfigAssign } from './config-url-fallback.js';
import { objectIdentity } from './object-identity.js';
import { configDeclarations } from './config-bindings.js';

export type ConfigReaders = { readonly shared: boolean; readonly constructors: readonly Node[] };

/** Prove that removing a field cannot affect a non-constructor reader. */
export function configReaders(input: Node, mapping: Mapping): ConfigReaders {
  let shared = false;
  const constructors = new Set<Node>();
  const seen = new Set<Node>();
  const visit = (value: Node): void => {
    if (seen.has(value)) return;
    seen.add(value);
    const parent = value.getParent();
    if (parent && unwrap(parent) === value) { visit(parent); return; }
    if (Node.isNewExpression(parent) && parent.getArguments()[0] === value && isClientConstructor(parent.getExpression(), mapping)) {
      // A local subclass may itself read the config before forwarding it.
      for (const declaration of new Set([...declarations(parent.getExpression()), objectIdentity(parent.getExpression())])) {
        if (!Node.isClassDeclaration(declaration) && !Node.isClassExpression(declaration)) continue;
        if (declaration.getSourceFile().isDeclarationFile()) continue;
        for (const constructor of declaration.getConstructors()) {
          const parameter = constructor.getParameters()[0];
          if (!parameter) continue;
          const name = parameter.getNameNode();
          if (!Node.isIdentifier(name)) { shared = true; continue; }
          for (const reference of name.findReferencesAsNodes()) if (reference !== name) visit(reference);
        }
      }
      constructors.add(parent);
      return;
    }
    if (Node.isCallExpression(parent)) {
      const callee = unwrap(parent.getExpression());
      if (parent.getArguments()[0] === value && callee.getKind() === SyntaxKind.SuperKeyword) {
        const owner = parent.getFirstAncestorByKind(SyntaxKind.Constructor)?.getParent();
        const base = Node.isClassDeclaration(owner) || Node.isClassExpression(owner) ? owner.getExtends()?.getExpression() : undefined;
        if (base && inheritsClientConstructor(base, mapping)) { constructors.add(parent); return; }
      }
      if (isMemberAccess(callee)) {
        const receiver = unwrap(callee.getExpression());
        if (memberName(callee) === 'bind' && parent.getArguments().indexOf(value) >= 1 && inheritsClientConstructor(receiver, mapping)) {
          constructors.add(parent); return;
        }
        const program = value.getProject().getProgram().compilerObject;
        const definitions = receiver.getSymbol()?.getDeclarations() ?? [];
        const reflect = Node.isIdentifier(receiver) && receiver.getText() === 'Reflect' && memberName(callee) === 'construct' &&
          definitions.length > 0 && definitions.every(definition => program.isSourceFileDefaultLibrary(definition.getSourceFile().compilerNode));
        const target = parent.getArguments()[0];
        if (reflect && target && parent.getArguments()[1] === value && inheritsClientConstructor(target, mapping)) {
          constructors.add(parent); return;
        }
      }
      if (isConfigAssign(parent) && parent.getArguments().includes(value)) {
        const target = parent.getArguments()[0];
        if (Node.isIdentifier(target)) {
          for (const declaration of configDeclarations(target, target.getSymbol()?.getDeclarations() ?? [])) {
            const init = Node.isVariableDeclaration(declaration) ? declaration.getInitializer() : undefined;
            if (init) visit(init);
            else shared = true;
          }
        } else if (target && target !== value) visit(target);
        visit(parent);
        return;
      }
    }
    if (Node.isExpressionStatement(parent) && Node.isCallExpression(value) && isConfigAssign(value)) return;
    if (Node.isSpreadAssignment(parent) || Node.isObjectLiteralExpression(parent) || Node.isArrayLiteralExpression(parent) || Node.isSpreadElement(parent)) {
      visit(parent); return;
    }
    if (Node.isVariableDeclaration(parent) && parent.getInitializer() === value) {
      const name = parent.getNameNode();
      if (!Node.isIdentifier(name)) { shared = true; return; }
      if (parent.getVariableStatement()?.isExported()) shared = true;
      for (const reference of name.findReferencesAsNodes()) if (reference !== name) visit(reference);
      return;
    }
    if (parent && isMemberAccess(parent) && parent.getExpression() === value) {
      const assignment = parent.getParent();
      // A standalone assignment is a writer, not an additional reader.
      if (Node.isBinaryExpression(assignment) && assignment.getLeft() === parent &&
        assignment.getOperatorToken().getKind() >= SyntaxKind.FirstAssignment &&
        assignment.getOperatorToken().getKind() <= SyntaxKind.LastAssignment && Node.isExpressionStatement(assignment.getParent())) return;
    }
    shared = true;
  };
  const left = Node.isBinaryExpression(input) ? input.getLeft() : undefined;
  const object = left && isMemberAccess(left) ? left.getExpression() : input;
  if (Node.isIdentifier(object)) {
    // Preserve import ownership, but discard JS expando write pseudo-declarations.
    const definitions = configDeclarations(object, object.getSymbol()?.getDeclarations() ?? []);
    if (!definitions.length) shared = true;
    for (const declaration of definitions) {
      if (Node.isVariableDeclaration(declaration)) {
        const initializer = declaration.getInitializer();
        if (initializer) visit(initializer);
        else shared = true;
      } else shared = true; // Parameters/imports/bindings may be owned by an unseen caller.
    }
    // Even opaque owners can expose local constructors that need a FIXME.
    if (shared) for (const reference of object.findReferencesAsNodes()) visit(reference);
  } else visit(object);
  return { shared, constructors: [...constructors].filter(node => node.getSourceFile() === input.getSourceFile()) };
}
