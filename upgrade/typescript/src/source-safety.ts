/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ts } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Item } from './model.js';
import { sourceLocation } from './edits.js';
import { isCloudApiUrl } from './cloud-api-url.js';
import { constantKey, sourceEnvUrls } from './env-api-url.js';
import type { EnvironmentContext } from './env-api-url.js';
import { ASSERTED_CONFIG, UNRESOLVED_CONFIG } from './config.js';

// Generated files get URL safety checks only; never rewrite them.
export function skippedSourceUrls(source: SourceFile, root: string, context: EnvironmentContext): readonly Item[] {
  if (!/daytona/i.test(source.getFullText())) return [];
  const items: Item[] = [...sourceEnvUrls(source, root, context)];
  let customLiterals = 0;
  // Keep the compiler-node fast path for ordinary keys in large bundles.
  const constant = (node: ts.Node): string | undefined => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
    const wrapped = source.getDescendantAtStartWithWidth(node.getStart(source.compilerNode), node.getWidth(source.compilerNode));
    return wrapped ? constantKey(wrapped) : undefined;
  };
  const propertyKey = (name: ts.Node): string | undefined => {
    if (ts.isIdentifier(name)) return name.text;
    if (ts.isPropertyAccessExpression(name)) return name.name.text;
    if (ts.isComputedPropertyName(name)) return constant(name.expression);
    if (ts.isElementAccessExpression(name)) return constant(name.argumentExpression);
    return constant(name);
  };
  const record = (node: ts.Node, key: string | undefined, value: ts.Expression | undefined): void => {
    if ((key !== 'apiUrl' && key !== 'serverUrl') || !value) return;
    const literal = ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value) ? value.text : undefined;
    if (literal === '' || literal !== undefined && isCloudApiUrl(literal, context.mapping)) return;
    if (literal !== undefined && ++customLiterals > 5) return;
    const unresolved = literal === undefined;
    items.push({ ...sourceLocation(source, node.getStart(source.compilerNode), root),
      kind: unresolved && context.dropApiUrl ? 'config-field' : 'custom-api-url', message: !unresolved
        ? 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.' : context.dropApiUrl ? ASSERTED_CONFIG : UNRESOLVED_CONFIG });
  };
  const pending: ts.Node[] = [source.compilerNode];
  for (let node = pending.pop(); node; node = pending.pop()) {
    const name = ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node) || ts.isPropertyDeclaration(node) ? node.name : ts.isBinaryExpression(node) &&
      node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment ? node.left : undefined;
    const value = ts.isPropertyAssignment(node) || ts.isPropertyDeclaration(node) ? node.initializer : ts.isShorthandPropertyAssignment(node) ? node.name :
      ts.isBinaryExpression(node) ? node.right : undefined;
    if (name) record(node, propertyKey(name), value);
    if (ts.isReturnStatement(node)) {
      let owner: ts.Node | undefined = node.parent;
      while (owner && !ts.isFunctionLike(owner)) owner = owner.parent;
      if (owner && ts.isGetAccessorDeclaration(owner)) record(node, propertyKey(owner.name), node.expression);
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === 'defineProperty' && ts.isIdentifier(node.expression.expression) &&
      ['Object', 'Reflect'].includes(node.expression.expression.text)) {
      const [, key, descriptor] = node.arguments;
      if (key && descriptor && ts.isObjectLiteralExpression(descriptor)) for (const property of descriptor.properties) {
        if (ts.isPropertyAssignment(property) && propertyKey(property.name) === 'value') record(node, constant(key), property.initializer);
        if ((ts.isMethodDeclaration(property) || ts.isPropertyAssignment(property)) && propertyKey(property.name) === 'get') {
          const getter = ts.isMethodDeclaration(property) ? property :
            ts.isArrowFunction(property.initializer) || ts.isFunctionExpression(property.initializer) ? property.initializer : undefined;
          if (getter && ts.isArrowFunction(getter) && !ts.isBlock(getter.body)) record(node, constant(key), getter.body);
          else if (getter && getter.body) {
            const returns: ts.Node[] = [getter.body];
            for (let returned = returns.pop(); returned; returned = returns.pop()) {
              if (returned !== getter.body && ts.isFunctionLike(returned)) continue;
              if (ts.isReturnStatement(returned) && returned.expression) record(node, constant(key), returned.expression);
              ts.forEachChild(returned, child => { returns.push(child); });
            }
          }
        }
      }
    }
    ts.forEachChild(node, child => { pending.push(child); });
  }
  if (customLiterals > 5) {
    const position = sourceLocation(source, 0, root);
    items.push({ ...position, kind: 'custom-api-url', message: `${customLiterals - 5} more custom URL literals in ${position.file}` });
  }
  return items;
}
