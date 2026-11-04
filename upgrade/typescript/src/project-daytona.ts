/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { Project, SourceFile } from 'ts-morph';
import { isModuleLoader } from './module-loader.js';

const evidence = new WeakMap<Project, boolean>();
const importEvidence = new WeakMap<Project, boolean>();

export function importsDaytonaSdk(source: SourceFile): boolean {
  return source.getDescendantsOfKind(SyntaxKind.StringLiteral).some(literal => {
    if (!/^@(daytonaio|daytona)\/sdk(?:\/v0)?$/.test(literal.getLiteralValue())) return false;
    const parent = literal.getParent();
    return Node.isImportDeclaration(parent) || Node.isExportDeclaration(parent) || Node.isExternalModuleReference(parent) ||
      Node.isCallExpression(parent) && (isModuleLoader(parent) || parent.getExpression().getKind() === SyntaxKind.ImportKeyword) ||
      Node.isLiteralTypeNode(parent) && Node.isImportTypeNode(parent.getParent());
  });
}

export function registerDaytonaEvidence(projects: readonly Project[], sources: readonly SourceFile[], dependency = false): void {
  const present = sources.some(importsDaytonaSdk);
  for (const project of projects) {
    evidence.set(project, present || dependency);
    importEvidence.set(project, present);
  }
}

export function projectImportsDaytona(source: SourceFile): boolean {
  return importEvidence.get(source.getProject()) ?? source.getProject().getSourceFiles()
    .filter(source => !source.getFilePath().replaceAll('\\', '/').includes('/node_modules/')).some(importsDaytonaSdk);
}

export function projectDependsOnDaytona(source: SourceFile): boolean {
  return evidence.get(source.getProject()) ?? projectImportsDaytona(source);
}
