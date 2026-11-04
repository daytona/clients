/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import type { BindingElement } from 'ts-morph';

export function bindingKey(entry: BindingElement): string | undefined {
  const property = entry.getPropertyNameNode();
  if (!property) return entry.getName();
  const key = Node.isComputedPropertyName(property) ? property.getExpression() : property;
  if (Node.isStringLiteral(key) || Node.isNoSubstitutionTemplateLiteral(key)) return key.getLiteralValue();
  return Node.isComputedPropertyName(property) ? undefined : key.getText();
}

// Decoded key of an object-literal member: `'api\u0055rl'` and `api\u0055rl`
// both name `apiUrl` at runtime. Undefined when the key is not static.
export function propertyKey(property: Node): string | undefined {
  if (Node.isSpreadAssignment(property)) return undefined;
  const name = (property as Node & { getNameNode?: () => Node }).getNameNode?.();
  if (!name) return undefined;
  const key = Node.isComputedPropertyName(name) ? name.getExpression() : name;
  if (Node.isStringLiteral(key) || Node.isNoSubstitutionTemplateLiteral(key)) return key.getLiteralValue();
  if (Node.isNumericLiteral(key)) return String(key.getLiteralValue());
  return Node.isIdentifier(key) && !Node.isComputedPropertyName(name) ? key.compilerNode.text : undefined;
}
