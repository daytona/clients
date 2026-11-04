/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { propertyKey } from './binding-key.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import type { Edit } from './model.js';
import type { Receivers } from './receivers.js';
import { bindingIsStable } from './stable-bindings.js';
import { migratedParameter } from './local-double-consumers.js';
import type { DoubleConsumers } from './local-double-consumers.js';
import { deepRootMock, mockFactory } from './mock-factories.js';
import { unresolvedClientDoubles } from './unresolved-client-doubles.js';
import { factoryMockTargets } from './factory-mock-targets.js';

function bareAnnotation(node: Node): boolean {
  if (!Node.isAsExpression(node) && !Node.isTypeAssertion(node) && !Node.isSatisfiesExpression(node)) return true;
  const annotation = node.getTypeNode();
  return annotation === undefined || ['any', 'unknown', 'const'].includes(annotation.getText());
}

/** A whole-object argument is evidence only at a proven root-client parameter. */
function clientArgument(node: Node, context: DoubleConsumers): boolean {
  let argument = node;
  while (argument.getParent() && unwrap(argument.getParentOrThrow(), true) === node) {
    argument = argument.getParentOrThrow();
    if (!bareAnnotation(argument)) return false;
  }
  const call = argument.getParent();
  if (!Node.isCallExpression(call)) return false;
  const position = call.getArguments().indexOf(argument);
  if (position < 0 || call.getArguments().some(Node.isSpreadElement)) return false;
  const signatures = call.getExpression().getType().getCallSignatures();
  return signatures.length > 0 && signatures.every(signature => {
    const parameters = signature.getParameters()[position]?.getDeclarations() ?? [];
    return parameters.length > 0 && parameters.every(parameter => {
      if (!Node.isParameterDeclaration(parameter)) return false;
      if (migratedParameter(parameter, context)) return true;
      const owner = parameter.getParent();
      if (Node.isFunctionDeclaration(owner) && !owner.getBody() && owner.getImplementation() !== undefined &&
          context.sources.has(owner.getSourceFile()) &&
          !owner.getSourceFile().isDeclarationFile() && context.receivers.client(parameter)) context.reviews.add(argument);
      return false;
    });
  });
}

function localShape(node: Node, receivers: Receivers): boolean {
  // A cast to some other domain type must not become root-client evidence.
  if (!bareAnnotation(node)) return false;
  if (Node.isAsExpression(node) || Node.isTypeAssertion(node) || Node.isSatisfiesExpression(node) ||
      Node.isParenthesizedExpression(node) || Node.isNonNullExpression(node)) return localShape(node.getExpression(), receivers);
  const value = node;
  if (Node.isCallExpression(value) && mockFactory(value)) return deepRootMock(value, receivers) ||
    value.getTypeArguments().every(argument => ['any', 'unknown'].includes(argument.getText()));
  if (!Node.isObjectLiteralExpression(value)) return false;
  const keys = value.getProperties().map(propertyKey);
  return keys.every(key => key !== undefined && receivers.mapping.movedClientMembers.ts.includes(key)) &&
    new Set(keys).size === keys.length && !value.getDescendantsOfKind(SyntaxKind.ThisKeyword).length &&
    value.getProperties().every(property => Node.isPropertyAssignment(property) || Node.isShorthandPropertyAssignment(property));
}

function shapeEdits(node: Node): readonly Edit[] {
  const value = unwrap(node, true);
  // Keep the literal's trivia and member evaluation order; only add a container.
  return [
    { start: value.getStart(), end: value.getStart(), text: '{ v0: ' },
    { start: value.getEnd(), end: value.getEnd(), text: ' }' },
  ];
}

function preserveAccess(access: Node, accesses: Set<Node>): void {
  let value = access;
  accesses.add(value);
  let parent = value.getParent();
  while (parent && (unwrap(parent, true) === unwrap(value, true) ||
      isMemberAccess(parent) && parent.getExpression() === value)) {
    value = parent;
    accesses.add(value);
    parent = value.getParent();
  }
}

export function localClientDoubles(source: SourceFile, receivers: Receivers, sources: ReadonlySet<SourceFile>): {
  readonly edits: readonly Edit[]; readonly accesses: ReadonlySet<Node>; readonly reviews: ReadonlySet<Node>;
} {
  const edits: Edit[] = [];
  const accesses = new Set<Node>();
  const reviews = new Set<Node>();
  const adapted = new Set<Node>();
  const context = { receivers, sources, reviews };
  for (const variable of source.getDescendantsOfKind(SyntaxKind.VariableDeclaration)) {
    const name = variable.getNameNode();
    const init = variable.getInitializer();
    const annotation = variable.getTypeNode();
    if (!Node.isIdentifier(name) || !init || !bindingIsStable(variable) || variable.isExported() ||
        annotation && !['any', 'unknown'].includes(annotation.getText()) || !localShape(init, receivers)) continue;
    const uses = name.findReferencesAsNodes();
    const members: Node[] = [];
    let supplied = false;
    const safe = uses.every(use => {
      if (use.getSourceFile() !== source) return false;
      if (clientArgument(use, context)) { supplied = true; return true; }
      const parent = use.getParent();
      if (!parent || !isMemberAccess(parent) || parent.getExpression() !== use ||
          !receivers.mapping.movedClientMembers.ts.includes(memberName(parent) ?? '')) return false;
      members.push(parent);
      return true;
    });
    if (!safe || !supplied) continue;
    if (!deepRootMock(unwrap(init, true), receivers)) edits.push(...shapeEdits(init));
    adapted.add(variable);
    for (const access of members) {
      if (!isMemberAccess(access)) continue;
      const receiver = access.getExpression();
      const optional = access.getFirstChildByKind(SyntaxKind.QuestionDotToken);
      const start = optional ? Node.isPropertyAccessExpression(access) ? access.getNameNode().getStart() : optional.getEnd() : receiver.getEnd();
      edits.push({ start, end: start, text: optional ? Node.isPropertyAccessExpression(access) ? 'v0.' : 'v0' : '.v0' });
      preserveAccess(access, accesses);
    }
  }
  for (const call of source.getDescendantsOfKind(SyntaxKind.CallExpression)) {
    for (const argument of call.getArguments()) {
      const value = unwrap(argument, true);
      if ((Node.isObjectLiteralExpression(value) || mockFactory(value)) && localShape(argument, receivers) && clientArgument(value, context)) {
        if (!deepRootMock(value, receivers)) edits.push(...shapeEdits(value));
        adapted.add(value);
      }
    }
  }
  unresolvedClientDoubles(source, context, adapted);
  factoryMockTargets(source, context);
  for (const review of reviews) {
    const value = unwrap(review, true);
    if (!Node.isIdentifier(value)) continue;
    for (const use of value.findReferencesAsNodes()) {
      const access = use.getParent();
      if (use.getSourceFile() === source && access && isMemberAccess(access) && access.getExpression() === use) preserveAccess(access, accesses);
    }
  }
  return { edits, accesses, reviews };
}
