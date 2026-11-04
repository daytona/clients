/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isAbsolute, relative } from 'node:path';
import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { declarations } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { memberName } from './member-access.js';

/** Tracks project value providers, never SDK declarations or unrelated module exports. */
export class OutsideProviders {
  constructor(private readonly source: SourceFile, private readonly root: string,
    private readonly reported: Set<string> = new Set()) {}

  messages(receiver: Node): readonly string[] {
    const providers = new Set<string>();
    const seen = new Set<Node>();
    const record = (node: Node): void => {
      const source = node.getSourceFile();
      const file = source.getFilePath();
      if (source.isDeclarationFile() || file.replaceAll('\\', '/').includes('/node_modules/')) return;
      const path = relative(this.root, file).replaceAll('\\', '/');
      if (path === '..' || path.startsWith('../') || isAbsolute(path)) providers.add(path);
    };
    const visit = (node: Node): void => {
      const value = unwrap(node);
      if (seen.has(value)) return;
      seen.add(value);
      if (value.getSourceFile().isDeclarationFile() || value.getSourceFile().getFilePath().replaceAll('\\', '/').includes('/node_modules/')) return;
      if (Node.isNewExpression(value)) {
        record(value);
        visit(value.getExpression());
        return;
      }
      if (Node.isCallExpression(value)) {
        visit(value.getExpression());
        return;
      }
      if (Node.isConditionalExpression(value)) {
        visit(value.getWhenTrue());
        visit(value.getWhenFalse());
        return;
      }
      if (Node.isBinaryExpression(value)) {
        visit(value.getLeft());
        visit(value.getRight());
        return;
      }
      if (Node.isFunctionDeclaration(value) || Node.isFunctionExpression(value) || Node.isArrowFunction(value) ||
          Node.isMethodDeclaration(value) || Node.isGetAccessorDeclaration(value)) {
        record(value);
        const body = value.getBody();
        if (body && !Node.isBlock(body)) visit(body);
        for (const returned of body?.getDescendantsOfKind(SyntaxKind.ReturnStatement) ?? []) {
          const owner = returned.getAncestors().find(parent => Node.isFunctionDeclaration(parent) || Node.isFunctionExpression(parent) ||
            Node.isArrowFunction(parent) || Node.isMethodDeclaration(parent) || Node.isGetAccessorDeclaration(parent));
          const expression = returned.getExpression();
          if (owner === value && expression) visit(expression);
        }
        return;
      }
      const init = initializer(value);
      if (init) {
        record(value);
        visit(init);
        return;
      }
      if (Node.isExportAssignment(value)) {
        visit(value.getExpression());
        return;
      }
      if (Node.isElementAccessExpression(value)) {
        const name = memberName(value);
        if (name !== undefined) for (const declaration of value.getExpression().getType().getProperty(name)?.getDeclarations() ?? []) visit(declaration);
        return;
      }
      for (const declaration of declarations(Node.isPropertyAccessExpression(value) ? value.getNameNode() : value)) {
        if (declaration === value) record(value);
        else visit(declaration);
      }
    };
    visit(receiver);
    const consumer = relative(this.root, this.source.getFilePath()).replaceAll('\\', '/');
    return [...providers].filter(provider => !this.reported.has(provider)).map(provider => {
      this.reported.add(provider);
      return `${consumer} uses a Daytona client from ${provider}, outside the scanned folder; migrate that file too.`;
    });
  }
}
