/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { declarations } from './bindings.js';
import { isMemberAccess } from './member-access.js';

// JavaScript expando writes contribute receiver identifiers to the variable's
// symbol. They describe property storage, not new bindings or reassignments.
export function configDeclarations(node: Node, definitions: readonly Node[] = declarations(node)): readonly Node[] {
  return definitions.filter(definition => {
    if (!Node.isIdentifier(definition) || !/\.(?:[cm]?js|jsx)$/.test(definition.getSourceFile().getFilePath())) return true;
    const access = definition.getParent();
    const assignment = access?.getParent();
    return !(isMemberAccess(access) && access.getExpression() === definition &&
      Node.isBinaryExpression(assignment) && assignment.getLeft() === access &&
      assignment.getOperatorToken().getKind() >= SyntaxKind.FirstAssignment &&
      assignment.getOperatorToken().getKind() <= SyntaxKind.LastAssignment);
  });
}
