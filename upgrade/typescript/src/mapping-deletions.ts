/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';
import { isObjectAssign } from './intrinsics.js';

function mappingOrigin(input: Node, seen: Set<Node> = new Set()): Node | undefined {
  const value = unwrap(input);
  if (!Node.isIdentifier(value) || seen.has(value)) return undefined;
  seen.add(value);
  const declarations = value.getSymbol()?.getDeclarations() ?? [];
  const declaration = declarations.length === 1 ? declarations[0] : undefined;
  if (!Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return undefined;
  const initializer = declaration.getInitializer();
  if (!initializer) return undefined;
  const initial = unwrap(initializer);
  if (Node.isIdentifier(initial)) return mappingOrigin(initial, seen);
  return Node.isObjectLiteralExpression(initial) && initial.getProperties().every(property =>
    Node.isPropertyAssignment(property) && !Node.isComputedPropertyName(property.getNameNode()) && property.getName() !== '__proto__')
    ? declaration : undefined;
}

// A deliberately narrow proof: a plain mapping, aliases/writes/deletes, then a
// single top-level copy. Calls, branches, loops and closures invalidate it.
export function mappingDeleteCutoffs(input: Node, keyOf: (node: Node) => string | undefined): ReadonlyMap<string, number> {
  const cutoffs = new Map<string, number>();
  const call = input.getParent();
  const copy = call?.getParent();
  if (!Node.isCallExpression(call) || !isObjectAssign(call) || call.getArguments().length !== 2 ||
    call.getArguments()[1] !== input || !Node.isExpressionStatement(copy) || !Node.isSourceFile(copy.getParent())) return cutoffs;
  const origin = mappingOrigin(input);
  const declaration = origin?.getFirstAncestor(Node.isVariableStatement);
  if (!origin || !declaration || declaration.getDeclarations().length !== 1 || declaration.getParent() !== copy.getParent()) return cutoffs;
  const statements = input.getSourceFile().getStatements();
  for (const statement of statements.slice(statements.indexOf(declaration) + 1, statements.indexOf(copy))) {
    if (Node.isVariableStatement(statement) && statement.getDeclarations().every(binding => {
      const initializer = binding.getInitializer();
      return initializer !== undefined && mappingOrigin(initializer) === origin && bindingIsStable(binding);
    })) continue;
    if (!Node.isExpressionStatement(statement)) return new Map();
    const expression = unwrap(statement.getExpression());
    if (Node.isDeleteExpression(expression)) {
      const target = unwrap(expression.getExpression());
      if (!(Node.isPropertyAccessExpression(target) || Node.isElementAccessExpression(target)) ||
        mappingOrigin(target.getExpression()) !== origin) return new Map();
      const key = keyOf(target);
      if (key === undefined) return new Map();
      cutoffs.set(key, expression.getStart());
      continue;
    }
    if (!Node.isBinaryExpression(expression) || expression.getOperatorToken().getText() !== '=') return new Map();
    const target = unwrap(expression.getLeft());
    const value = unwrap(expression.getRight());
    if (!(Node.isPropertyAccessExpression(target) || Node.isElementAccessExpression(target)) ||
      mappingOrigin(target.getExpression()) !== origin || keyOf(target) === undefined ||
      !(Node.isStringLiteral(value) || Node.isNoSubstitutionTemplateLiteral(value))) return new Map();
    cutoffs.delete(keyOf(target) ?? '');
  }
  return cutoffs;
}
