/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { declarations } from './bindings.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { configValue } from './config-references.js';
import { bindingIsStable } from './stable-bindings.js';
import { isObjectAssign } from './intrinsics.js';

const conversionHooks = new Set(['toString', 'valueOf', '__proto__']);

function hasPlainConversion(value: Node): boolean {
  return Node.isObjectLiteralExpression(value) && !value.getProperties().some(property =>
    Node.isSpreadAssignment(property) || Node.isComputedPropertyName(property.getNameNode()) ||
    conversionHooks.has(property.getName().replace(/^['"]|['"]$/g, '')));
}

function plainStringArgument(node: Node): boolean {
  const value = configValue(node);
  if (!hasPlainConversion(value)) return false;
  if (node.getSourceFile().getDescendantsOfKind(SyntaxKind.CallExpression).some(call => {
    if (!isObjectAssign(call)) return false;
    const target = call.getArguments()[0];
    return target !== undefined && configValue(target) === value &&
      call.getArguments().slice(1).some(argument => !hasPlainConversion(configValue(argument)));
  })) return false;
  return !node.getSourceFile().getDescendantsOfKind(SyntaxKind.BinaryExpression).some(assignment => {
    const operator = assignment.getOperatorToken().getKind();
    const access = assignment.getLeft();
    if (operator < SyntaxKind.FirstAssignment || operator > SyntaxKind.LastAssignment || !isMemberAccess(access) ||
        configValue(access.getExpression()) !== value) return false;
    const name = memberName(access);
    return name === undefined || conversionHooks.has(name);
  });
}

function globalReadOnly(node: Node): boolean {
  if (!Node.isIdentifier(node)) return false;
  const definitions = node.getSymbol()?.getDeclarations() ?? [];
  const program = node.getProject().getProgram().compilerObject;
  return definitions.length > 0 && definitions.every(definition =>
    program.isSourceFileDefaultLibrary(definition.getSourceFile().compilerNode) ||
    /\/node_modules\/@types\/node\/(?:console|globals)\.d\.ts$/.test(definition.getSourceFile().getFilePath()));
}

function utilImport(node: Node, named: boolean): boolean {
  const definitions = node.getSymbol()?.getDeclarations() ?? [];
  return definitions.length === 1 && definitions.every(definition => {
    const imported = definition.getFirstAncestorByKind(SyntaxKind.ImportDeclaration);
    if (!imported || !['node:util', 'util'].includes(imported.getModuleSpecifierValue())) return false;
    if (named) return Node.isImportSpecifier(definition) && definition.getName() === 'inspect' && !definition.isTypeOnly() && !imported.isTypeOnly();
    return Node.isNamespaceImport(definition) || Node.isImportClause(definition) && !definition.isTypeOnly();
  });
}

// Trust bindings, never a callee's spelling alone (local console/JSON/util
// objects and shadowing parameters may mutate the config).
export function isConfigReadOnlyCall(node: Node): boolean {
  if (!Node.isCallExpression(node)) return false;
  const callee = unwrap(node.getExpression());
  if (Node.isIdentifier(callee)) {
    if (callee.getText() === 'String' && globalReadOnly(callee) && declarations(callee).every(bindingIsStable)) {
      const argument = node.getArguments()[0];
      return argument !== undefined && node.getArguments().length === 1 && plainStringArgument(argument);
    }
    return utilImport(callee, true);
  }
  if (!isMemberAccess(callee)) return false;
  const receiver = unwrap(callee.getExpression());
  const name = memberName(callee);
  if (Node.isIdentifier(receiver) && globalReadOnly(receiver)) {
    if (receiver.getText() === 'console') return true;
    if (receiver.getText() === 'JSON' && name === 'stringify') {
      const argument = node.getArguments()[1];
      if (!argument) return true;
      const replacer = unwrap(argument);
      // A replacer callback receives the original config, not just its text.
      return Node.isNullLiteral(replacer) || Node.isIdentifier(replacer) &&
        replacer.getText() === 'undefined' && declarations(replacer).length === 0 ||
        Node.isArrayLiteralExpression(replacer) && replacer.getElements().every(element =>
          Node.isStringLiteral(element) || Node.isNumericLiteral(element));
    }
  }
  return name === 'inspect' && utilImport(receiver, false);
}
