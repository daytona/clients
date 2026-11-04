/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { Edit, Item, Mapping } from './model.js';
import { item, replace, unwrap } from './edits.js';
import { isNamespace } from './bindings.js';
import { bindingIsStable } from './stable-bindings.js';
import { importStyle } from './import-style.js';
import { isMemberAccess, memberName } from './member-access.js';
import { missingV0Export, staysAtRoot } from './v0-exports.js';
import { bindingKey } from './binding-key.js';

export function namespaceImports(binding: Node, owner: Node, context: {
  readonly mapping: Mapping; readonly root: string; readonly commonJs: boolean;
}): { readonly edits: readonly Edit[]; readonly items: readonly Item[] } {
  const { mapping, root, commonJs } = context;
  const source = binding.getSourceFile();
  const style = importStyle(source, mapping);
  const edits: Edit[] = [];
  const protectedDeclarations = new Set<Node>();
  const items: Item[] = [];
  const manual = (node: Node): void => {
    items.push(item(node, { kind: 'private-import', message: 'Review escaping or reassigned Daytona namespace uses manually; resource exports now live in @daytona/sdk/v0.' }, root));
  };
  if (!isNamespace(binding, mapping)) { manual(binding); return { edits, items }; }
  const stays = (name: string): boolean => staysAtRoot(name, mapping);
  const names = new Set(source.getDescendantsOfKind(SyntaxKind.Identifier).map(node => node.getText()));
  let alias = `${binding.getText()}V0`;
  while (names.has(alias)) alias += '_';
  const moved = (node: Node, name: string): boolean => {
    if (stays(name)) return false;
    items.push(...missingV0Export(node, name, context));
    return true;
  };
  const pending = [binding];
  const visited = new Set<Node>();
  for (const current of pending) {
    if (visited.has(current)) continue;
    visited.add(current);
    const symbol = current.getSymbol();
    for (const declaration of source.getExportDeclarations()) {
      for (const specifier of declaration.getNamedExports()) {
        if (symbol && specifier.getLocalTargetSymbol() === symbol) manual(specifier);
      }
    }
    for (const use of source.getDescendantsOfKind(SyntaxKind.Identifier)) {
      if (!symbol || use === current || use.getSymbol() !== symbol) continue;
      let value: Node = use;
      while (Node.isParenthesizedExpression(value.getParent())) value = value.getParentOrThrow();
      const parent = value.getParent();
      if (!parent) continue;
      if ((isMemberAccess(parent) && parent.getExpression() === value) ||
        Node.isQualifiedName(parent) && parent.getLeft() === value) {
        const name = isMemberAccess(parent) ? memberName(parent) : parent.getRight().getText();
        if (name === undefined) manual(parent);
        else if (moved(parent, name)) edits.push(replace(value, alias));
        continue;
      }
      if (Node.isVariableDeclaration(parent) && parent.getInitializer() && unwrap(parent.getInitializerOrThrow()) === use) {
        const name = parent.getNameNode();
        if (Node.isIdentifier(name)) {
          if (parent.getVariableStatement()?.isExported()) manual(parent);
          if (bindingIsStable(parent)) pending.push(name);
          else manual(parent);
          continue;
        }
        if (Node.isObjectBindingPattern(name)) {
          const entries = name.getElements();
          const resources = entries.filter(entry => {
            const key = bindingKey(entry);
            if (entry.getDotDotDotToken() || key === undefined) { manual(entry); return false; }
            return moved(entry, key);
          });
          const kept = entries.filter(entry => !resources.includes(entry));
          if (kept.length && resources.length && name.getDescendants().some(node => isMemberAccess(node) &&
            isNamespace(node.getExpression(), mapping) && !stays(memberName(node) ?? ''))) {
            protectedDeclarations.add(parent); manual(parent); continue;
          }
          if (!resources.length) continue;
          if (!kept.length) edits.push(replace(value, alias));
          else {
            edits.push(replace(name, `{ ${kept.map(entry => entry.getText()).join(', ')} }`));
            edits.push({ start: parent.getEnd(), end: parent.getEnd(), text: `, { ${resources.map(entry => entry.getText()).join(', ')} } = ${alias}` });
          }
          continue;
        }
      }
      manual(use);
    }
  }
  const safeEdits = edits.filter(edit => ![...protectedDeclarations].some(declaration =>
    edit.start >= declaration.getStart() && edit.end <= declaration.getEnd()));
  if (safeEdits.length) {
    const module = commonJs ? owner.getDescendantsOfKind(SyntaxKind.StringLiteral)
      .find(literal => mapping.v0Modules.ts.old.includes(literal.getLiteralValue())) : undefined;
    const quote = module?.getText()[0] ?? style.quote;
    if (commonJs) {
      const variable = binding.getFirstAncestorByKindOrThrow(SyntaxKind.VariableDeclaration);
      safeEdits.push({ start: variable.getEnd(), end: variable.getEnd(), text: `, ${alias} = require(${quote}${mapping.v0Modules.ts.v0}${quote})` });
    } else {
      const declaration = Node.isImportEqualsDeclaration(owner) ?
        `import ${owner.isTypeOnly() ? 'type ' : ''}${alias} = require(${style.quote}${mapping.v0Modules.ts.v0}${style.quote})${style.semicolon}` :
        `import ${Node.isImportDeclaration(owner) && owner.isTypeOnly() ? 'type ' : ''}* as ${alias} from ${quote}${mapping.v0Modules.ts.v0}${quote}${style.semicolon}`;
      safeEdits.push({ start: owner.getEnd(), end: owner.getEnd(), text: style.newline + declaration });
    }
  }
  return { edits: safeEdits, items };
}
