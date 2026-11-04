/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { bindingKey } from './binding-key.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { isModuleLiteral, isModuleLoader } from './module-loader.js';
import { bindingIsStable } from './stable-bindings.js';

const testModules = ['vitest', '@jest/globals'] as const;
const moduleHelpers: readonly string[] = ['mock', 'doMock', 'unmock', 'dontMock', 'requireActual', 'requireMock', 'importActual', 'importMock'];
// Module-taking helpers that exist on only one framework's object.
const jestOnly: readonly string[] = ['unstable_mockModule', 'deepUnmock', 'createMockFromModule', 'genMockFromModule'];
const viOnly: readonly string[] = ['doUnmock'];

function testModule(module: string | undefined): boolean {
  return testModules.some(name => name === module);
}

function testNamespace(node: Node): boolean {
  const value = unwrap(node);
  if (Node.isCallExpression(value) && isModuleLoader(value)) {
    const module = value.getArguments()[0];
    return isModuleLiteral(module) && testModule(module.getLiteralValue());
  }
  if (!Node.isIdentifier(value)) return false;
  return (value.getSymbol()?.getDeclarations() ?? []).some(declaration => {
    if (Node.isNamespaceImport(declaration)) {
      return testModule(declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierValue());
    }
    if (!Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return false;
    const init = declaration.getInitializer();
    // Only a module loader establishes a CJS namespace, not arbitrary aliases.
    return Node.isCallExpression(init) && isModuleLoader(init) && testNamespace(init);
  });
}

function testExport(node: Node): string | undefined {
  const value = unwrap(node);
  if (isMemberAccess(value) && testNamespace(value.getExpression())) return memberName(value);
  if (!Node.isIdentifier(value)) return undefined;
  const declarations = value.getSymbol()?.getDeclarations() ?? [];
  for (const declaration of declarations) {
    if (Node.isImportSpecifier(declaration)) {
      const module = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration)?.getModuleSpecifierValue();
      if (testModule(module)) return declaration.getName();
    }
    if (Node.isBindingElement(declaration) && bindingIsStable(declaration)) {
      const variable = declaration.getFirstAncestorByKind(SyntaxKind.VariableDeclaration);
      const init = variable?.getInitializer();
      if (declaration.getParent() === variable?.getNameNode() && !declaration.getDotDotDotToken() &&
          !declaration.getInitializer() && init && testNamespace(init)) return bindingKey(declaration);
    }
  }
  if (['jest', 'vi'].includes(value.getText()) && declarations.every(entry => entry.getSourceFile().isDeclarationFile())) return value.getText();
  return undefined;
}

export function testHelper(node: Node): string | undefined {
  const value = unwrap(node);
  const exported = testExport(value);
  if (exported !== undefined) return exported;
  if (!isMemberAccess(value)) return undefined;
  const tester = testExport(value.getExpression());
  return tester === 'jest' || tester === 'vi' ? memberName(value) : undefined;
}

export function isTestModuleHelper(node: Node): boolean {
  const value = unwrap(node);
  const helper = testHelper(value);
  if (helper === undefined) return false;
  if (moduleHelpers.includes(helper)) return true;
  const tester = isMemberAccess(value) ? testExport(value.getExpression()) : undefined;
  return tester === 'jest' && jestOnly.includes(helper) || tester === 'vi' && viOnly.includes(helper);
}
