/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { CallExpression, Project } from 'ts-morph';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { isSdkV0Access } from './sdk-v0-access.js';

const callsByProject = new WeakMap<Project, readonly CallExpression[]>();
const identities = new WeakMap<Node, Node>();

/** Syntax identity, deliberately independent of the SDK's shared member declarations. */
export function objectIdentity(node: Node, seen: Set<Node> = new Set()): Node {
  const cached = identities.get(node);
  if (cached) return cached;
  const identity = resolveIdentity(node, seen);
  identities.set(node, identity);
  return identity;
}

function resolveIdentity(node: Node, seen: Set<Node>): Node {
  const value = unwrap(node);
  if (seen.has(value)) return value;
  seen.add(value);
  // The SDK declares one getter for every client; its result belongs to the
  // receiver instance, not to that shared declaration. Unresolved namespaces
  // retain that syntax identity too, without establishing SDK provenance.
  if (isMemberAccess(value) && memberName(value) === 'v0' &&
      (isSdkV0Access(value) || (value.getSymbol()?.getDeclarations().length ?? 0) === 0)) {
    return objectIdentity(value.getExpression(), seen);
  }
  const parent = value.getParent();
  const symbol = Node.isShorthandPropertyAssignment(parent) && parent.getNameNode() === value
    ? parent.getValueSymbol() : value.getSymbol();
  const declarations = (symbol?.getAliasedSymbol() ?? symbol)?.getDeclarations() ?? [];
  for (const declaration of declarations) {
    const init = initializer(declaration);
    if (init) return objectIdentity(init, seen);
  }
  if (Node.isThisExpression(value) || Node.isSuperExpression(value)) {
    // Arrows inherit this; ordinary nested functions bind their own receiver.
    // Class methods/accessors and function-valued class fields use the instance.
    for (const parent of value.getAncestors()) {
      if (Node.isClassDeclaration(parent) || Node.isClassExpression(parent)) return parent;
      if ((!Node.isFunctionLikeDeclaration(parent) && !Node.isFunctionExpression(parent)) || Node.isArrowFunction(parent)) continue;
      // Object literals are values, not receiver scopes. Only their methods and
      // accessors bind this; property initializers and arrows inherit it.
      if (Node.isMethodDeclaration(parent) || Node.isConstructorDeclaration(parent) ||
          Node.isGetAccessorDeclaration(parent) || Node.isSetAccessorDeclaration(parent)) return parent.getParentOrThrow();
      if (Node.isFunctionExpression(parent) && Node.isPropertyDeclaration(parent.getParent())) continue;
      return parent;
    }
    return value;
  }
  return declarations[0] ?? value;
}

export function projectCalls(node: Node): readonly CallExpression[] {
  const project = node.getProject();
  const cached = callsByProject.get(project);
  if (cached) return cached;
  const calls = project.getSourceFiles().filter(source => !source.isDeclarationFile())
    .flatMap(source => source.getDescendantsOfKind(SyntaxKind.CallExpression));
  callsByProject.set(project, calls);
  return calls;
}

/** Different scopes/files have no provable execution order, so retain their effects. */
export function mayPrecede(effect: Node, use: Node): boolean {
  if (effect.getSourceFile() !== use.getSourceFile()) return true;
  const scope = (node: Node): Node | undefined => node.getFirstAncestor(Node.isFunctionLikeDeclaration);
  if (scope(effect) !== scope(use) || effect.getEnd() <= use.getStart()) return true;
  return effect.getAncestors().some(parent => {
    const loop = Node.isForStatement(parent) || Node.isForOfStatement(parent) || Node.isForInStatement(parent) ||
      Node.isWhileStatement(parent) || Node.isDoStatement(parent);
    const call = parent.getParent();
    const callee = Node.isCallExpression(call) ? unwrap(call.getExpression()) : undefined;
    const callback = Node.isFunctionLikeDeclaration(parent) && Node.isCallExpression(call) && call.getArguments().includes(parent) &&
      callee !== undefined && isMemberAccess(callee) && ['forEach', 'map'].includes(memberName(callee) ?? '');
    return (loop || callback) && use.getAncestors().includes(parent);
  });
}
