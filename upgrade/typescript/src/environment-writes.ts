/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, ts } from 'ts-morph';
import { isModuleLoader } from './module-loader.js';

// Recognize explicit Node process sources without mistaking local lookalikes for globals.
export function isProcessSource(node: Node): boolean {
  if (Node.isCallExpression(node) && isModuleLoader(node)) {
    const args = node.getArguments();
    const module = args[0];
    return args.length === 1 && (Node.isStringLiteral(module) || Node.isNoSubstitutionTemplateLiteral(module)) &&
      ['process', 'node:process'].includes(module.getLiteralValue());
  }
  if (!(Node.isPropertyAccessExpression(node) || Node.isElementAccessExpression(node))) return false;
  const receiver = node.getExpression();
  const key = Node.isPropertyAccessExpression(node) ? node.getName() : node.getArgumentExpression();
  const name = typeof key === 'string' ? key : Node.isStringLiteral(key) || Node.isNoSubstitutionTemplateLiteral(key) ? key.getLiteralValue() : undefined;
  return name === 'process' && Node.isIdentifier(receiver) && ['globalThis', 'global', 'window', 'self'].includes(receiver.getText()) &&
    (receiver.getSymbol()?.getDeclarations() ?? []).every(declaration => {
      const parent = declaration.getParent();
      // JS expando writes synthesize identifiers, not user binding declarations.
      return declaration.getSourceFile().isDeclarationFile() || Node.isIdentifier(declaration) &&
        (Node.isPropertyAccessExpression(parent) || Node.isElementAccessExpression(parent)) && parent.getExpression() === declaration;
    });
}

// Compiler-node traversal avoids ts-morph wrapper creation for large pure bundles.
export function mayWriteEnvironment(node: ts.Node): boolean {
  const pending: ts.Node[] = [node];
  let processReference = false;
  let writes = false;
  let current = pending.pop();
  while (current) {
    processReference ||= ts.isIdentifier(current) && current.text === 'process' ||
      (ts.isStringLiteral(current) || ts.isNoSubstitutionTemplateLiteral(current)) && (current.text === 'process' || current.text === 'node:process');
    writes ||= ts.isCallExpression(current) || ts.isForInStatement(current) || ts.isForOfStatement(current) ||
      ts.isBinaryExpression(current) && current.operatorToken.kind >= ts.SyntaxKind.FirstAssignment &&
        current.operatorToken.kind <= ts.SyntaxKind.LastAssignment;
    if (processReference && writes) return true;
    ts.forEachChild(current, child => { pending.push(child); });
    current = pending.pop();
  }
  return false;
}
