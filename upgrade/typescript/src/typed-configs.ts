/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, ts } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { importedName } from './bindings.js';
import type { Mapping } from './model.js';
import { isModuleLiteral } from './module-loader.js';

// Only explicit annotations bound to the SDK opt an otherwise unused config in.
export function typedConfigs(source: SourceFile, mapping: Mapping): readonly Node[] {
  const values: Node[] = [];
  const isConfig = (annotation: Node | undefined): boolean => {
    if (Node.isParenthesizedTypeNode(annotation)) return isConfig(annotation.getTypeNode());
    if (Node.isImportTypeNode(annotation)) {
      const argument = annotation.getArgument();
      const module = Node.isLiteralTypeNode(argument) ? argument.getLiteral() : undefined;
      return !annotation.compilerNode.isTypeOf && annotation.getQualifier()?.getText() === 'DaytonaConfig' &&
        isModuleLiteral(module) && mapping.v0Modules.ts.old.includes(module.getLiteralValue().replace(/\/v0$/, ''));
    }
    return Node.isTypeReference(annotation) && importedName(annotation.getTypeName(), mapping) === 'DaytonaConfig';
  };
  for (const node of source.getDescendants()) {
    if (Node.isVariableDeclaration(node) && isConfig(node.getTypeNode())) {
      const init = node.getInitializer();
      if (init) values.push(init);
    } else if ((Node.isSatisfiesExpression(node) || Node.isAsExpression(node)) && isConfig(node.getTypeNode())) {
      values.push(node.getExpression());
    } else if ((Node.isFunctionDeclaration(node) || Node.isFunctionExpression(node) || Node.isArrowFunction(node) ||
      Node.isMethodDeclaration(node) || Node.isGetAccessorDeclaration(node)) && isConfig(node.getReturnTypeNode())) {
      const body = node.getBody();
      if (!body) continue;
      if (!Node.isBlock(body)) { values.push(body); continue; }
      for (const returned of body.getDescendantsOfKind(SyntaxKind.ReturnStatement)) {
        if (returned.getFirstAncestor(ancestor => ts.isFunctionLike(ancestor.compilerNode)) !== node) continue;
        const expression = returned.getExpression();
        if (expression) values.push(expression);
      }
    }
  }
  return values;
}
