/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Edit, Item, Mapping } from './model.js';
import { item, replace } from './edits.js';
import { importEqualsModule, isRequire } from './bindings.js';
import { importStyle } from './import-style.js';
import { isModuleLiteral, isModuleLoader } from './module-loader.js';
import { isMemberAccess, memberName } from './member-access.js';
import { namespaceImports } from './namespace-imports.js';
import { missingV0Export, staysAtRoot } from './v0-exports.js';
import { bindingKey } from './binding-key.js';
import { moduleAugmentations } from './module-augmentations.js';
import { isTestModuleHelper } from './test-bindings.js';
import { namedList } from './named-list.js';

export type ImportResult = { readonly edits: readonly Edit[]; readonly items: readonly Item[] };
export function imports(source: SourceFile, mapping: Mapping, root: string): ImportResult {
  const augmentations = moduleAugmentations(source, mapping, root);
  const edits: Edit[] = [...augmentations.edits];
  const items: Item[] = [...augmentations.items];
  const handled = new Set<Node>();
  const splitBindings: (() => void)[] = [];
  const style = importStyle(source, mapping);
  const stays = (name: string): boolean => staysAtRoot(name, mapping);
  const namespace = (binding: Node, owner: Node, commonJs: boolean): void => {
    const result = namespaceImports(binding, owner, { mapping, root, commonJs });
    edits.push(...result.edits);
    items.push(...result.items);
  };
  for (const declaration of source.getDescendantsOfKind(SyntaxKind.ImportEqualsDeclaration)) {
    const module = importEqualsModule(declaration);
    if (!module || !mapping.v0Modules.ts.old.includes(module.getLiteralValue())) continue;
    handled.add(module);
    edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
    namespace(declaration.getNameNode(), declaration, false);
  }
  for (const declaration of source.getImportDeclarations()) {
    const module = declaration.getModuleSpecifier();
    if (!mapping.v0Modules.ts.old.includes(module.getLiteralValue())) continue;
    handled.add(module);
    const named = declaration.getNamedImports();
    const moved = named.filter(entry => !stays(entry.getName()));
    for (const entry of moved) items.push(...missingV0Export(entry, entry.getName(), { mapping, root }));
    const kept = named.filter(entry => !moved.includes(entry));
    if (moved.length === named.length && moved.length > 0 && !declaration.getDefaultImport()) {
      edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.v0)));
    } else if (moved.length) {
      const brace = declaration.getFirstDescendantByKindOrThrow(SyntaxKind.NamedImports);
      if (kept.length) edits.push(replace(brace, namedList(brace, kept, style.newline)));
      else {
        const comma = brace.getPreviousSiblingIfKindOrThrow(SyntaxKind.CommaToken);
        const trivia = source.getFullText().slice(comma.getEnd(), brace.getStart());
        edits.push({ start: comma.getStart(), end: brace.getEnd(), text: trivia.trim() ? trivia : '' });
      }
      edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
      edits.push({ start: declaration.getEnd(), end: declaration.getEnd(), text:
        `${splitComments(declaration, style.newline)}import ${declaration.isTypeOnly() ? 'type ' : ''}${namedList(brace, moved, style.newline)} from ${style.quote}${mapping.v0Modules.ts.v0}${style.quote}${style.semicolon}` });
    } else if (module.getLiteralValue() !== mapping.v0Modules.ts.root) {
      edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
    }
    const binding = declaration.getNamespaceImport();
    if (binding) namespace(binding, declaration, false);
    if (declaration.getDefaultImport()) items.push(item(declaration, { kind: 'private-import', message: 'Review default Daytona import; use the named Daytona export.' }, root));
  }
  for (const declaration of source.getExportDeclarations()) {
    const module = declaration.getModuleSpecifier();
    if (!module || !mapping.v0Modules.ts.old.includes(module.getLiteralValue())) continue;
    handled.add(module);
    const named = declaration.getNamedExports();
    const moved = named.filter(entry => !stays(entry.getName()));
    for (const entry of moved) items.push(...missingV0Export(entry, entry.getName(), { mapping, root }));
    const kept = named.filter(entry => !moved.includes(entry));
    if (!named.length) items.push(item(declaration, { kind: 'private-import', message: 'Review export *: resource exports and the 0.x error classes now live in @daytona/sdk/v0.' }, root));
    if (moved.length && kept.length) {
      const brace = declaration.getFirstDescendantByKindOrThrow(SyntaxKind.NamedExports);
      edits.push(replace(brace, namedList(brace, kept, style.newline)));
      edits.push({ start: declaration.getEnd(), end: declaration.getEnd(), text:
        `${splitComments(declaration, style.newline)}export ${declaration.isTypeOnly() ? 'type ' : ''}${namedList(brace, moved, style.newline)} from ${style.quote}${mapping.v0Modules.ts.v0}${style.quote}${style.semicolon}` });
    }
    edits.push(replace(module, quoteModule(module, moved.length && !kept.length ? mapping.v0Modules.ts.v0 : mapping.v0Modules.ts.root)));
  }
  for (const call of source.getDescendantsOfKind(SyntaxKind.CallExpression)) {
    const argument = call.getArguments()[0];
    if (isModuleLiteral(argument) && mapping.v0Modules.ts.old.includes(argument.getLiteralValue()) &&
        (isRequire(call, mapping) || call.getExpression().getKind() === SyntaxKind.ImportKeyword)) {
      let receiver: Node = call;
      let access = receiver.getParent();
      let moduleValue = isRequire(call, mapping);
      while (Node.isParenthesizedExpression(access) || Node.isAwaitExpression(access)) {
        if (Node.isAwaitExpression(access)) moduleValue = true;
        receiver = access;
        access = receiver.getParent();
      }
      if (moduleValue && access && isMemberAccess(access) && access.getExpression() === receiver) {
        const name = memberName(access);
        if (name !== undefined) {
          const moved = name !== 'default' && !stays(name);
          handled.add(argument);
          edits.push(replace(argument, quoteModule(argument, moved ? mapping.v0Modules.ts.v0 : mapping.v0Modules.ts.root)));
          if (moved) items.push(...missingV0Export(access, name, { mapping, root }));
          if (name === 'default') items.push(item(access, { kind: 'private-import',
            message: 'Review default Daytona import; use the named Daytona export.' }, root));
          continue;
        }
      }
    }
    if (Node.isTemplateExpression(argument) && /daytona/i.test(argument.getText()) &&
        (call.getExpression().getKind() === SyntaxKind.ImportKeyword || isModuleLoader(call) || isTestModuleHelper(call.getExpression()))) {
      items.push(item(argument, { kind: 'dynamic-import', message:
        'Review dynamic import: SDK 2.x client methods live on .v0; resource exports now live in @daytona/sdk/v0.' }, root));
    }
    if (!isRequire(call, mapping)) continue;
    const module = call.getArguments()[0];
    if (!isModuleLiteral(module) || !mapping.v0Modules.ts.old.includes(module.getLiteralValue())) continue;
    handled.add(module);
    const variable = call.getParent();
    if (!Node.isVariableDeclaration(variable)) {
      edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
      items.push(item(call, { kind: 'dynamic-import', message: 'Review require target after upgrading to SDK 2.x.' }, root));
      continue;
    }
    const binding = variable.getNameNode();
    if (Node.isObjectBindingPattern(binding)) {
      const moved = binding.getElements().filter(entry => {
        if (entry.getDotDotDotToken()) return false;
        const key = bindingKey(entry);
        if (key === undefined) {
          items.push(item(entry, { kind: 'private-import', message: 'Review computed Daytona import key manually; its root or v0 namespace cannot be proven.' }, root));
          return false;
        }
        if (stays(key)) return false;
        items.push(...missingV0Export(entry, key, { mapping, root }));
        return true;
      });
      const kept = binding.getElements().filter(entry => !moved.includes(entry));
      for (const rest of kept.filter(entry => entry.getDotDotDotToken() !== undefined)) {
        const symbol = rest.getNameNode().getSymbol();
        for (const use of source.getDescendants().filter(node => Node.isPropertyAccessExpression(node) || Node.isElementAccessExpression(node))) {
          if (!symbol || use.getExpression().getSymbol() !== symbol) continue;
          const name = memberName(use);
          if (name === undefined || !stays(name)) items.push(item(use, { kind: 'private-import',
            message: 'Review resource access through a CommonJS rest binding; resources now live in @daytona/sdk/v0.' }, root));
        }
      }
      if (moved.length && kept.length) {
        const annotation = variable.getTypeNode();
        const typeArgument = Node.isImportTypeNode(annotation) ? annotation.getArgument() : undefined;
        const typeModule = Node.isLiteralTypeNode(typeArgument) ? typeArgument.getLiteral() : undefined;
        if (annotation && (!Node.isImportTypeNode(annotation) || !annotation.compilerNode.isTypeOf ||
            annotation.getQualifier() || annotation.getTypeArguments().length || annotation.compilerNode.attributes ||
            !isModuleLiteral(typeModule) || typeModule.getLiteralValue() !== module.getLiteralValue())) {
          items.push(item(binding, { kind: 'private-import', message:
            'Review this typed CommonJS destructuring import manually: its annotation cannot be safely split between root and v0 exports.' }, root));
          edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
          continue;
        }
        const statement = variable.getVariableStatement();
        const parent = statement?.getParent();
        if (!statement || !(Node.isSourceFile(parent) || Node.isBlock(parent) || Node.isCaseClause(parent) || Node.isDefaultClause(parent))) {
          items.push(item(binding, { kind: 'private-import', message: 'Review this CommonJS destructuring import manually: splitting it requires a statement block.' }, root));
          edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
          continue;
        }
        const v0Annotation = annotation && isModuleLiteral(typeModule) ? `: typeof import(${quoteModule(typeModule, mapping.v0Modules.ts.v0)})` : '';
        if (annotation && isModuleLiteral(typeModule)) {
          handled.add(typeModule);
          edits.push(replace(typeModule, quoteModule(typeModule, mapping.v0Modules.ts.root)));
        }
        // Compose inner require/default edits only after every module loader has been visited.
        splitBindings.push(() => {
          const nested = edits.filter(edit => edit.start >= binding.getStart() && edit.end <= binding.getEnd());
          for (const edit of nested) edits.splice(edits.indexOf(edit), 1);
          const rendering = { newline: style.newline, edits: nested };
          edits.push(replace(binding, namedList(binding, kept, rendering)));
          const indent = source.getFullText().slice(0, statement.getStart()).match(/(?:^|\n)([\t ]*)$/u)?.[1] ?? '';
          const semicolon = statement.getText().endsWith(';') ? ';' : '';
          const keyword = `${statement.isExported() ? 'export ' : ''}${statement.getDeclarationKind()}`;
          edits.push({ start: variable.getEnd(), end: variable.getEnd(), text:
            `${semicolon}${style.newline}${indent}${keyword} ${namedList(binding, moved, rendering)}${v0Annotation} = require(${quoteModule(module, mapping.v0Modules.ts.v0)})` });
        });
      }
      edits.push(replace(module, quoteModule(module, moved.length && !kept.length ? mapping.v0Modules.ts.v0 : mapping.v0Modules.ts.root)));
    } else {
      edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
      namespace(binding, variable.getFirstAncestorByKindOrThrow(SyntaxKind.VariableStatement), true);
    }
  }
  for (const module of source.getDescendants().filter(isModuleLiteral)) {
    if (handled.has(module)) continue;
    const value = module.getLiteralValue();
    if (mapping.v0Modules.ts.keep.includes(value)) continue;
    const parent = module.getParent();
    const typeReference = Node.isLiteralTypeNode(parent) ? parent.getParent() : undefined;
    if (Node.isImportTypeNode(typeReference) && mapping.v0Modules.ts.old.includes(value)) {
      const qualifier = typeReference.getQualifier();
      if (!Node.isIdentifier(qualifier)) {
        // A bare `import('mod')` type names the whole module (moved to v0);
        // `typeof import('mod')` queries the module's value-side exports
        // (the namespace object type), so its module specifier stays at root.
        if (!qualifier) edits.push(replace(module, quoteModule(module, typeReference.compilerNode.isTypeOf ? mapping.v0Modules.ts.root : mapping.v0Modules.ts.v0)));
        items.push(item(typeReference, { kind: 'private-import', message:
          'Review this Daytona type reference manually; its root or v0 export cannot be proven.' }, root));
        continue;
      }
      const name = qualifier.getText();
      if (!stays(name)) items.push(...missingV0Export(typeReference, name, { mapping, root }));
      edits.push(replace(module, quoteModule(module, stays(name) ? mapping.v0Modules.ts.root : mapping.v0Modules.ts.v0)));
      continue;
    }
    const mock = Node.isCallExpression(parent) && parent.getArguments()[0] === module && isTestModuleHelper(parent.getExpression());
    const isSpecifier = Node.isImportDeclaration(parent) || Node.isExportDeclaration(parent) || Node.isExternalModuleReference(parent) ||
      Node.isLiteralTypeNode(parent) && Node.isImportTypeNode(parent.getParent()) ||
      Node.isCallExpression(parent) && parent.getArguments()[0] === module && (parent.getExpression().getKind() === SyntaxKind.ImportKeyword ||
        isModuleLoader(parent) || mock);
    if (!isSpecifier) continue;
    if (mapping.v0Modules.ts.old.some(name => value.startsWith(`${name}/`))) {
      items.push(item(module, { kind: 'private-import', message: `Private Daytona import ${value}: move to a public @daytona/sdk or @daytona/sdk/v0 export.` }, root));
    } else if (mapping.v0Modules.ts.old.includes(value)) {
      edits.push(replace(module, quoteModule(module, mapping.v0Modules.ts.root)));
      items.push(item(module, { kind: mock ? 'mock-target' : 'dynamic-import', message: mock ?
        'Review mock target: SDK 2.x client methods live on .v0; @daytona/sdk/v0 needs its own mock.' : dynamicImportMessage(module) }, root));
    }
  }
  for (const split of splitBindings) split();
  return { edits, items };
}
function dynamicImportMessage(module: Node): string {
  const message = 'Review dynamic import: SDK 2.x client methods live on .v0; resource exports now live in @daytona/sdk/v0';
  const loader = module.getParent();
  const call = loader?.getFirstAncestorByKind(SyntaxKind.CallExpression);
  const adjacent = Node.isCallExpression(loader) && loader.getExpression().getKind() === SyntaxKind.ImportKeyword &&
    call?.getArguments().filter(Node.isObjectLiteralExpression).some(argument => argument.getProperties().some(property => {
      const value = Node.isPropertyAssignment(property) ? property.getInitializer() : undefined;
      return isModuleLiteral(value) && value.getLiteralValue() === '@daytonaio/sdk';
    }));
  return adjacent ? `${message}; also update the adjacent '@daytonaio/sdk' string` : `${message}.`;
}
function quoteModule(node: Node, value: string): string {
  const quote = node.getText()[0] ?? "'";
  return `${quote}${value}${quote}`;
}
function splitComments(declaration: Node, newline: string): string {
  // Insertion precedes the original trailing comments, so copy them to the root line.
  const text = declaration.getSourceFile().getFullText();
  const trailing = text.slice(declaration.getEnd())
    .match(/^(?:[\t ]*(?:\/\/[^\r\n\u2028\u2029]*|\/\*[^\r\n\u2028\u2029]*?\*\/))+/u);
  const leading = declaration.getLeadingCommentRanges().at(-1);
  const nextLine = leading && /^(?:\/\/|\/\*)\s*eslint-disable-next-line(?:\s|\*\/|$)/u.test(leading.getText()) &&
    /^[\t ]*(?:\r\n|[\r\n\u2028\u2029])[\t ]*$/u.test(text.slice(leading.getEnd(), declaration.getStart()))
    ? `${leading.getText()}${newline}` : '';
  return `${trailing?.[0] ?? ''}${newline}${nextLine}`;
}
