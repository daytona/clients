/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { ImportResult } from './imports.js';
import type { Mapping } from './model.js';
import { item, replace } from './edits.js';
import { staysAtRoot } from './v0-exports.js';

export function moduleAugmentations(source: SourceFile, mapping: Mapping, root: string): ImportResult {
  const edits: ImportResult['edits'][number][] = [];
  const items: ImportResult['items'][number][] = [];
  for (const declaration of source.getDescendantsOfKind(SyntaxKind.ModuleDeclaration)) {
    const name = declaration.getNameNode();
    if (!Node.isStringLiteral(name)) continue;
    const value = name.getLiteralValue();
    if (mapping.v0Modules.ts.keep.includes(value) || !mapping.v0Modules.ts.old.some(module =>
      value === module || value.startsWith(`${module}/`))) continue;
    const body = declaration.getBody();
    let replacedClient = false;
    const targets = Node.isModuleBlock(body) ? body.getStatements().map(statement => {
      if (!Node.isInterfaceDeclaration(statement) && !Node.isClassDeclaration(statement) &&
        !Node.isFunctionDeclaration(statement) && !Node.isTypeAliasDeclaration(statement) && !Node.isEnumDeclaration(statement)) return undefined;
      const exported = statement.getName();
      if (!exported) return undefined;
      if (['Daytona', 'DaytonaConfig'].includes(exported)) { replacedClient = true; return undefined; }
      if (staysAtRoot(exported, mapping)) return mapping.v0Modules.ts.root;
      return mapping.v0Exports?.ts.includes(exported) ? mapping.v0Modules.ts.v0 : undefined;
    }) : [];
    const target = targets[0];
    if (target !== undefined && targets.every(candidate => candidate === target)) {
      const quote = name.getText()[0] ?? "'";
      edits.push(replace(name, `${quote}${target}${quote}`));
    } else items.push(item(name, { kind: 'private-import',
      message: replacedClient ? 'Review this module augmentation: the 0.x Daytona client is exported from @daytona/sdk/v0 as RootOnlyClientV0, and DaytonaConfig changed in SDK 2.x.' :
        'Review this module augmentation: SDK 2.x moved 0.x types to @daytona/sdk/v0.' }, root));
  }
  return { edits, items };
}
