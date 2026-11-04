/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, VariableDeclarationKind } from 'ts-morph';
import { configDeclarations } from './config-bindings.js';
import { bindingIsReassigned } from './stable-bindings.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';

// Resolve identity, not arbitrary descendants: cfg.apiKey is a value read,
// whereas holder.cfg can expose the config object itself.
export function configValue(node: Node, active: ReadonlySet<Node> = new Set()): Node {
  const value = unwrap(node);
  if (active.has(value)) return value;
  const path = new Set([...active, value]);
  if (Node.isIdentifier(value)) {
    const parent = value.getParent();
    const definitions = Node.isShorthandPropertyAssignment(parent)
      ? parent.getValueSymbol()?.getDeclarations() ?? [] : configDeclarations(value);
    const definition = definitions.length === 1 ? definitions[0] : undefined;
    if (Node.isVariableDeclaration(definition) && definition.getSourceFile() === value.getSourceFile() &&
        definition.getVariableStatement()?.getDeclarationKind() === VariableDeclarationKind.Const && !bindingIsReassigned(definition)) {
      const init = definition.getInitializer();
      if (init) return configValue(init, path);
    }
  }
  if (isMemberAccess(value)) {
    const owner = configValue(value.getExpression(), path);
    if (Node.isObjectLiteralExpression(owner)) {
      const property = owner.getProperty(memberName(value) ?? '');
      if (Node.isPropertyAssignment(property)) {
        const init = property.getInitializer();
        if (init) return configValue(init, path);
      }
      if (Node.isShorthandPropertyAssignment(property)) return configValue(property.getNameNode(), path);
    }
    if (Node.isArrayLiteralExpression(owner) && Node.isElementAccessExpression(value)) {
      const index = value.getArgumentExpression();
      const element = Node.isNumericLiteral(index) ? owner.getElements()[index.getLiteralValue()] : undefined;
      if (element) return configValue(element, path);
    }
  }
  return value;
}

export function containsConfig(node: Node, tracked: (value: Node) => boolean): boolean {
  const active = new Set<Node>();
  const contains = (expression: Node): boolean => {
    const value = configValue(expression);
    if (active.has(value)) return false;
    active.add(value);
    if (tracked(value)) return true;
    // A mutable initializer is not an identity proof, but it may expose config.
    if (Node.isIdentifier(value)) {
      const parent = value.getParent();
      const definitions = Node.isShorthandPropertyAssignment(parent)
        ? parent.getValueSymbol()?.getDeclarations() ?? [] : configDeclarations(value);
      return definitions.some(definition => {
        const init = Node.isVariableDeclaration(definition) && definition.getSourceFile() === value.getSourceFile()
          ? definition.getInitializer() : undefined;
        return init !== undefined && contains(init);
      });
    }
    if (Node.isObjectLiteralExpression(value)) return value.getProperties().some(property => {
      if (Node.isShorthandPropertyAssignment(property)) return contains(property.getNameNode());
      if (Node.isSpreadAssignment(property)) return contains(property.getExpression());
      const init = Node.isPropertyAssignment(property) ? property.getInitializer() : undefined;
      return init !== undefined && contains(init);
    });
    if (Node.isArrayLiteralExpression(value)) return value.getElements().some(element =>
      contains(Node.isSpreadElement(element) ? element.getExpression() : element));
    return false;
  };
  return contains(node);
}
