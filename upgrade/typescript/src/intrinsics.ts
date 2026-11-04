/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';

export function objectAssignReceiver(node: Node): Node | undefined {
  if (!Node.isCallExpression(node)) return undefined;
  const access = unwrap(node.getExpression());
  if (!isMemberAccess(access) || memberName(access) !== 'assign') return undefined;
  const receiver = unwrap(access.getExpression());
  return Node.isIdentifier(receiver) && receiver.getText() === 'Object' ? receiver : undefined;
}

export function isObjectAssign(node: Node): boolean {
  const receiver = objectAssignReceiver(node);
  if (!receiver) return false;
  const declarations = receiver.getSymbol()?.getDeclarations() ?? [];
  const program = node.getProject().getProgram().compilerObject;
  return declarations.length > 0 && declarations.every(declaration =>
    program.isSourceFileDefaultLibrary(declaration.getSourceFile().compilerNode));
}
