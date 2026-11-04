/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { propertyKey } from './binding-key.js';
import { declarations } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { bindingIsStable } from './stable-bindings.js';
import { testHelper } from './test-bindings.js';

/** Unknown control flow is not proof that a factory cannot fall through. */
function terminates(node: Node | undefined): boolean {
  if (Node.isReturnStatement(node) || Node.isThrowStatement(node)) return true;
  if (Node.isBlock(node)) return terminates(node.getStatements().at(-1));
  if (Node.isIfStatement(node)) return terminates(node.getThenStatement()) && terminates(node.getElseStatement());
  return false;
}

/** A null step invokes a visible replacement factory; strings select literal exports. */
export function replacementHasV0(node: Node, path: readonly (string | null)[], seen: ReadonlySet<Node> = new Set()): boolean {
  const value = unwrap(node, true);
  if (seen.has(value) || seen.size >= 64) return false;
  const next = new Set(seen).add(value);
  const follow = (target: Node, rest = path): boolean => replacementHasV0(target, rest, next);
  if (path.length === 1 && path[0] === null && (Node.isFunctionLikeDeclaration(value) ||
      Node.isCallExpression(value) && testHelper(value.getExpression()) === 'fn')) return true;
  if (!path.length && (Node.isFunctionLikeDeclaration(value) || Node.isObjectLiteralExpression(value) ||
      Node.isCallExpression(value) && testHelper(value.getExpression()) === 'fn')) return true;
  if (Node.isIdentifier(value)) {
    const targets = declarations(value);
    return targets.length > 0 && targets.every(target => follow(target));
  }
  const init = initializer(value);
  if (init) return bindingIsStable(value) && follow(init);
  if (Node.isObjectLiteralExpression(value)) {
    const [key, ...rest] = path;
    if (key === null || key === undefined) return false;
    const properties = value.getProperties();
    if (properties.some(property => propertyKey(property) === undefined)) return false;
    const matches = properties.filter(property => propertyKey(property) === key);
    const property = matches.length === 1 ? matches[0] : undefined;
    if (!property) return false;
    if (Node.isShorthandPropertyAssignment(property)) return follow(property.getNameNode(), rest);
    return follow(property, rest);
  }
  if (Node.isFunctionDeclaration(value) || Node.isFunctionExpression(value) || Node.isArrowFunction(value) || Node.isMethodDeclaration(value)) {
    if (path[0] !== null) return false;
    const body = value.getBody();
    if (!body) return false;
    const rest = path.slice(1);
    if (!Node.isBlock(body)) return follow(body, rest);
    const returns: Node[] = [];
    let complete = true;
    body.forEachDescendant((child, traversal) => {
      if (Node.isFunctionLikeDeclaration(child)) { traversal.skip(); return; }
      if (!Node.isReturnStatement(child)) return;
      const result = child.getExpression();
      if (result) returns.push(result); else complete = false;
    });
    return complete && terminates(body) && returns.every(result => follow(result, rest));
  }
  if (Node.isCallExpression(value) && path[0] === null) {
    const callee = unwrap(value.getExpression(), true);
    const argument = value.getArguments()[0];
    if (!argument) return false;
    if (testHelper(callee) === 'fn') return follow(argument);
    if (!isMemberAccess(callee)) return false;
    switch (memberName(callee)) {
      case 'mockReturnValue': return follow(argument, path.slice(1));
      case 'mockImplementation': return follow(argument);
      default: return false;
    }
  }
  return false;
}
