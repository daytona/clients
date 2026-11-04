/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { isMemberAccess, memberName } from './member-access.js';
import { ReceiverPackages } from './receiver-packages.js';

const packages = new ReceiverPackages();

/** Only the root SDK's namespace getter carries its owning client's identity. */
export function isSdkV0Access(node: Node): boolean {
  if (!isMemberAccess(node) || memberName(node) !== 'v0') return false;
  const declarations = node.getSymbol()?.getDeclarations() ?? [];
  return declarations.length > 0 && declarations.every(declaration => {
    if (!Node.isGetAccessorDeclaration(declaration)) return false;
    const owner = declaration.getParent();
    if (!Node.isClassDeclaration(owner) || owner.getName() !== 'Daytona') return false;
    const path = declaration.getSourceFile().getFilePath().replaceAll('\\', '/');
    const major = packages.version(path);
    const packagePath = packages.relativePath(path);
    return major !== undefined && major >= 2 && !packagePath.includes('/v0/') && !packagePath.includes('/v2/');
  });
}
