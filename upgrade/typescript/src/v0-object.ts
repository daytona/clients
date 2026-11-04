/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { bindingKey, propertyKey } from './binding-key.js';
import { declarations } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { bindingIsStable } from './stable-bindings.js';
import { bindingAssignments } from './binding-assignments.js';

// null is a call operation; strings are static property reads.
type Step = string | null;

/** Prove every traced alternative derives from an explicit .v0 member call. */
export function isV0Object(node: Node): boolean {
  // A per-query budget bounds branching as well as recursive depth. No cache:
  // callers may analyze the same project before and after import migration.
  let remaining = 256;
  const trace = (node: Node, path: readonly Step[], seen: ReadonlySet<Node>): boolean => {
    const value = unwrap(node);
    if (--remaining < 0 || seen.size >= 64 || seen.has(value)) return false;
    const context = new Set(seen).add(value);
    const follow = (next: Node, steps: readonly Step[] = path): boolean => trace(next, steps, context);
    if (Node.isConditionalExpression(value)) return follow(value.getWhenTrue()) && follow(value.getWhenFalse());
    if (Node.isBinaryExpression(value)) {
      const operator = value.getOperatorToken().getKind();
      if (operator === SyntaxKind.CommaToken) return follow(value.getRight());
      if ([SyntaxKind.BarBarToken, SyntaxKind.QuestionQuestionToken, SyntaxKind.AmpersandAmpersandToken].includes(operator)) {
        return follow(value.getLeft()) && follow(value.getRight());
      }
    }
    if (Node.isCallExpression(value)) return follow(value.getExpression(), [null, ...path]);
    if (!bindingIsStable(value)) return false;
    if (isMemberAccess(value)) {
      // A method itself is not an object result. Require its invocation,
      // including aliases/destructuring. A later call decides the result anew.
      if (memberName(value) === 'v0' && typeof path[0] === 'string' && path[1] === null && !path.slice(2).includes(null)) return true;
      const name = memberName(value);
      return name !== undefined && follow(value.getExpression(), [name, ...path]);
    }
    if (Node.isIdentifier(value)) {
      const targets = declarations(value);
      return targets.length > 0 && targets.every(target => follow(target));
    }
    if (Node.isBindingElement(value)) {
      if (value.getDotDotDotToken() || value.getInitializer()) return false;
      const keys: string[] = [];
      let entry = value;
      for (;;) {
        const pattern = entry.getParent();
        const key = Node.isArrayBindingPattern(pattern) ? String(pattern.getElements().indexOf(entry)) : bindingKey(entry);
        if (key === undefined) return false;
        keys.unshift(key);
        const owner = pattern.getParent();
        if (Node.isBindingElement(owner)) { entry = owner; continue; }
        const init = initializer(owner);
        return bindingIsStable(owner) && init !== undefined && follow(init, [...keys, ...path]);
      }
    }
    if (Node.isShorthandPropertyAssignment(value)) {
      const targets = value.getValueSymbol()?.getDeclarations() ?? [];
      return targets.length > 0 && targets.every(target => follow(target));
    }
    const init = initializer(value);
    if (init) return follow(init);
    if (Node.isExportAssignment(value)) return follow(value.getExpression());
    if (Node.isObjectLiteralExpression(value)) {
      const [key, ...rest] = path;
      if (typeof key !== 'string') return false;
      // Spreads and duplicate keys can replace the apparent property value.
      const properties = value.getProperties();
      if (properties.some(Node.isSpreadAssignment)) return false;
      const candidates = properties.filter(property => propertyKey(property) === key);
      return candidates.length === 1 && candidates.every(property => follow(property, rest));
    }
    if (Node.isArrayLiteralExpression(value)) {
      const [key, ...rest] = path;
      if (typeof key !== 'string' || !/^(0|[1-9]\d*)$/.test(key) || value.getElements().some(Node.isSpreadElement)) return false;
      const element = value.getElements()[Number(key)];
      return element !== undefined && follow(element, rest);
    }
    if (Node.isSourceFile(value)) {
      const [key, ...rest] = path;
      if (typeof key !== 'string') return false;
      const targets = value.getExportedDeclarations().get(key) ?? [];
      return targets.length > 0 && targets.every(target => follow(target, rest));
    }
    if (Node.isFunctionDeclaration(value) || Node.isFunctionExpression(value) || Node.isArrowFunction(value) ||
        Node.isMethodDeclaration(value) || Node.isGetAccessorDeclaration(value)) {
      // bindingIsStable indexes variable/property bindings, not declarations
      // introduced with `function`. Those names can also be reassigned.
      if (Node.isFunctionDeclaration(value) && value.getProject().getSourceFiles().some(source =>
        bindingAssignments(value, source).length > 0)) return false;
      const getter = Node.isGetAccessorDeclaration(value);
      if (!getter && path[0] !== null) return false;
      const rest = getter ? path : path.slice(1);
      const body = value.getBody();
      if (!body) return false;
      if (!Node.isBlock(body)) return follow(body, rest);
      const returns: Node[] = [];
      let complete = true;
      body.forEachDescendant((child, traversal) => {
        if (Node.isFunctionLikeDeclaration(child)) { traversal.skip(); return; }
        if (!Node.isReturnStatement(child)) return;
        const expression = child.getExpression();
        if (expression) returns.push(expression);
        else complete = false;
      });
      return complete && returns.length > 0 && returns.every(returned => follow(returned, rest));
    }
    return false;
  };
  return trace(node, [], new Set());
}
