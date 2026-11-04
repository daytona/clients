/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { typeKind } from './bindings.js';
import { isMemberAccess, memberName } from './member-access.js';
import type { Receivers } from './receivers.js';
import { testHelper } from './test-bindings.js';

export function mockFactory(node: Node): 'fn' | 'mock' | 'mockDeep' | undefined {
  if (!Node.isCallExpression(node)) return undefined;
  if (testHelper(node.getExpression()) === 'fn') return 'fn';
  const expression = node.getExpression();
  const binding = isMemberAccess(expression) ? expression.getExpression() : expression;
  if (!Node.isIdentifier(binding)) return undefined;
  for (const declaration of binding.getSymbol()?.getDeclarations() ?? []) {
    const imported = Node.isImportSpecifier(declaration) && binding === expression
      ? (declaration.compilerNode.propertyName ?? declaration.compilerNode.name).text
      : Node.isNamespaceImport(declaration) && isMemberAccess(expression) ? memberName(expression) : undefined;
    const module = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierValue();
    if ((imported === 'mock' || imported === 'mockDeep') &&
        (module === 'jest-mock-extended' || module === 'vitest-mock-extended')) return imported;
  }
  return undefined;
}

/** Only an empty deep root mock auto-vivifies a correctly typed v0 client. */
export function deepRootMock(node: Node, receivers: Receivers): boolean {
  if (!Node.isCallExpression(node) || mockFactory(node) !== 'mockDeep' || node.getArguments().length) return false;
  const [type, ...rest] = node.getTypeArguments();
  return type !== undefined && rest.length === 0 && typeKind(type, receivers.mapping) === 'client';
}
