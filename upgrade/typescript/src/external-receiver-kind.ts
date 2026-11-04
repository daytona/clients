/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { Type } from 'ts-morph';
import type { ReceiverPackages } from './receiver-packages.js';
import { unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';
import { isMemberAccess } from './member-access.js';

export function unresolvedType(type: Type): boolean {
  return type.isAny() || type.isUnknown() || type.isTypeParameter() ||
    type.isUnion() && type.getUnionTypes().some(unresolvedType) ||
    type.isIntersection() && type.getIntersectionTypes().some(unresolvedType);
}

/** The same external-package rule applies to receivers and traced factory callees. */
export function externalReceiverKind(node: Node, packages: ReceiverPackages): 'non-client' | undefined {
  const value = unwrap(node);
  const invoked = Node.isNewExpression(value) || Node.isCallExpression(value);
  let callee = unwrap(invoked ? value.getExpression() : value);
  while (isMemberAccess(callee)) callee = unwrap(callee.getExpression());
  for (const declaration of callee.getSymbol()?.getDeclarations() ?? []) {
    if (!bindingIsStable(declaration)) continue;
    const imported = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration);
    if (!imported) continue;
    const module = imported.getModuleSpecifierValue();
    if (/^(?:[./#]|@\/|@daytona(?:io)?\/sdk(?:\/|$))/.test(module)) continue;
    const paths = imported.getProject().getCompilerOptions().paths ?? {};
    if (Object.keys(paths).some(path => path === module || path.includes('*') &&
      module.startsWith(path.split('*')[0] ?? '') && module.endsWith(path.split('*')[1] ?? ''))) continue;
    const source = imported.getModuleSpecifierSourceFile();
    if (source && !source.getFilePath().replaceAll('\\', '/').includes('/node_modules/')) continue;
    // Explicit SDK result types are handled by the proof-grade path first.
    // Package provenance is negative even before installation or with any returns.
    if (!packages.identity(value)) return 'non-client';
  }
  return undefined;
}
