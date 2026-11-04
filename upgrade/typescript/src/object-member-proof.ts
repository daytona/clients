/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { propertyKey } from './binding-key.js';
import { isMemberAccess, memberName } from './member-access.js';
import { initializer, unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';

/** Object-literal symbols may point at the first definition, not the runtime value. */
export function ambiguousObjectProperty(node: Node): boolean {
  const object = node.getParent();
  if (!Node.isObjectLiteralExpression(object)) return false;
  const key = propertyKey(node);
  if (key === undefined) return true;
  const properties = object.getProperties();
  const matching = properties.filter(property => propertyKey(property) === key);
  const accessorPair = matching.length === 2 && matching.some(Node.isGetAccessorDeclaration) &&
    matching.some(Node.isSetAccessorDeclaration) && matching.every(bindingIsStable);
  return matching.length > 1 && !accessorPair ||
    properties.slice(properties.findIndex(property => property === node) + 1).some(property =>
      Node.isSpreadAssignment(property) || propertyKey(property) === undefined);
}

export function ambiguousObjectMember(node: Node, seen: ReadonlySet<Node> = new Set()): boolean {
  const value = unwrap(node);
  if (seen.has(value) || seen.size >= 64) return false;
  const next = new Set(seen).add(value);
  const providers = value.getSymbol()?.getDeclarations() ?? [];
  if (isMemberAccess(value)) {
    const property = value.getExpression().getType().getProperty(memberName(value) ?? '');
    return [...providers, ...property?.getDeclarations() ?? []].some(ambiguousObjectProperty);
  }
  return Node.isIdentifier(value) && providers.some(provider => {
    const init = initializer(provider);
    return init !== undefined && ambiguousObjectMember(init, next);
  });
}
