/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { daytonaModule, isClientConstructor } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import type { ExportProvenance } from './export-provenance.js';
import type { Origin } from './provenance-origin.js';
import { combined } from './provenance-origin.js';
import { importedReceiver } from './imported-receivers.js';
import { isProjectLocalModule } from './project-modules.js';
import { bindingAssignments } from './binding-assignments.js';

export function factoryOrigin(node: Node, seen: ReadonlySet<Node>, trace: ExportProvenance): Origin | undefined {
  const value = unwrap(node);
  if (seen.has(value) || seen.size >= 64) return 'unknown';
  const context = new Set(seen).add(value);
  if (Node.isExportDeclaration(value)) return trace.client(value, seen);
  if (Node.isBinaryExpression(value) && value.getOperatorToken().getKind() === SyntaxKind.CommaToken) {
    return factoryOrigin(value.getRight(), context, trace);
  }
  if (Node.isConditionalExpression(value)) {
    return combined([factoryOrigin(value.getWhenTrue(), context, trace) ?? 'unknown',
      factoryOrigin(value.getWhenFalse(), context, trace) ?? 'unknown']);
  }
  if (Node.isBinaryExpression(value) && [SyntaxKind.BarBarToken, SyntaxKind.QuestionQuestionToken,
    SyntaxKind.BarBarEqualsToken, SyntaxKind.QuestionQuestionEqualsToken].includes(value.getOperatorToken().getKind())) {
    const left = factoryOrigin(value.getLeft(), context, trace) ?? 'unknown';
    const right = factoryOrigin(value.getRight(), context, trace) ?? 'unknown';
    return left === 'undefined' ? right : combined([left, right]);
  }
  if (Node.isPropertyAccessExpression(value) || Node.isElementAccessExpression(value)) {
    // Only external package members establish a non-client origin, not local object methods.
    let receiver: Node = value;
    const path: (string | undefined)[] = [];
    while (Node.isPropertyAccessExpression(receiver) || Node.isElementAccessExpression(receiver)) {
      const property = Node.isPropertyAccessExpression(receiver) ? receiver.getName() : receiver.getArgumentExpression();
      path.unshift(typeof property === 'string' ? property : Node.isStringLiteral(property) ? property.getLiteralValue() : undefined);
      receiver = unwrap(receiver.getExpression());
    }
    const origin = importedReceiver(receiver);
    if (origin && origin.name === '*' && isProjectLocalModule(origin.source, origin.module, trace.workspaceNames)) {
      return trace.hintOrigin({ ...origin, path }, context, true);
    }
    return origin && !daytonaModule(origin.module, trace.mapping) &&
      !isProjectLocalModule(origin.source, origin.module, trace.workspaceNames) ? trace.hintOrigin(origin, context, true) : undefined;
  }
  const origin = importedReceiver(value);
  if (origin) {
    if (isProjectLocalModule(origin.source, origin.module, trace.workspaceNames)) return trace.hintOrigin(origin, context, true);
    if (daytonaModule(origin.module, trace.mapping)) { trace.linkedDaytona = true; return 'unknown'; }
    return trace.hintOrigin(origin, context, true);
  }
  if (Node.isIdentifier(value)) {
    const origins = (value.getSymbol()?.getDeclarations() ?? []).map(declaration => factoryOrigin(declaration, context, trace));
    return origins.some(origin => origin !== undefined) ? combined(origins.map(origin => origin ?? 'unknown')) : undefined;
  }
  if (Node.isParameterDeclaration(value)) return trace.parameterOrigin(value, context, true);
  if (Node.isBindingElement(value)) return trace.client(value, seen);
  if (Node.isClassDeclaration(value) || Node.isClassExpression(value)) {
    if (isClientConstructor(value, trace.mapping)) { trace.linkedDaytona = true; return 'daytona'; }
    return value.getExtends() || value.getConstructors().length ? 'unknown' : 'not-daytona';
  }
  if (Node.isVariableDeclaration(value)) {
    const init = initializer(value);
    const origins = [...(init ? [factoryOrigin(init, context, trace) ?? 'unknown'] : []), ...bindingAssignments(value).map(assignment =>
      Node.isBinaryExpression(assignment) && Node.isIdentifier(unwrap(assignment.getLeft())) &&
      [SyntaxKind.EqualsToken, SyntaxKind.BarBarEqualsToken, SyntaxKind.QuestionQuestionEqualsToken].includes(assignment.getOperatorToken().getKind()) ?
        factoryOrigin(assignment.getRight(), context, trace) ?? 'unknown' : 'unknown' as const)];
    if (origins.length) return combined(origins);
    // Ambient constructor variables (Map, Set, ...) have no implementation to
    // trace. Let the caller use the concrete new-expression result type instead.
    if (value.getFirstAncestorByKind(SyntaxKind.ModuleDeclaration) || value.getVariableStatement()?.isAmbient()) return undefined;
    return 'undefined';
  }
  const init = initializer(value);
  if (init) return factoryOrigin(init, context, trace);
  if (!Node.isFunctionDeclaration(value) && !Node.isFunctionExpression(value) && !Node.isArrowFunction(value) &&
    !Node.isMethodDeclaration(value) && !Node.isGetAccessorDeclaration(value)) return undefined;
  if ([...seen].filter(Node.isFunctionLikeDeclaration).length >= 4) return 'unknown';
  const body = value.getBody();
  if (!body) return 'unknown';
  if (!Node.isBlock(body)) return trace.client(body, context);
  const returns: Origin[] = [];
  body.forEachDescendant((child, traversal) => {
    if (Node.isFunctionLikeDeclaration(child)) { traversal.skip(); return; }
    if (!Node.isReturnStatement(child)) return;
    const expression = child.getExpression();
    returns.push(expression ? trace.client(expression, context) : 'unknown');
  });
  return returns.length ? combined(returns) : 'unknown';
}
