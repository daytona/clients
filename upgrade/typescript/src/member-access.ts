/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import type { ElementAccessExpression, PropertyAccessExpression } from 'ts-morph';

export type MemberAccess = PropertyAccessExpression | ElementAccessExpression;
export function memberName(node: Node): string | undefined {
  if (Node.isPropertyAccessExpression(node)) return node.getName();
  if (Node.isElementAccessExpression(node)) {
    const key = node.getArgumentExpression();
    if (Node.isStringLiteral(key) || Node.isNoSubstitutionTemplateLiteral(key)) return key.getLiteralValue();
  }
  return undefined;
}
export function isMemberAccess(node: Node): node is MemberAccess {
  return Node.isPropertyAccessExpression(node) || Node.isElementAccessExpression(node) && memberName(node) !== undefined;
}
