/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, readdirSync, statSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { location, unwrap } from './edits.js';
import { isModuleLiteral, isModuleLoader } from './module-loader.js';
import { bindingIsStable } from './stable-bindings.js';
import { importEqualsModule } from './bindings.js';
import { bindingKey } from './binding-key.js';
import type { Item } from './model.js';
import type { ReadContext, ReadFailures } from './read-failures.js';

type Origin = { readonly module: string; readonly member?: string; readonly uncertain?: true };
type Paths = { readonly candidates: readonly string[]; readonly cwdCandidate?: string };
const dotenvModules = ['dotenv', 'dotenvx', '@dotenvx/dotenvx', 'dotenv-flow'] as const;
const processModules = ['process', 'node:process'] as const;
export const DOTENV_LOAD_REVIEW = 'Review dotenv load: the file it loads may set DAYTONA_API_URL, which SDK 2.x .v0 ignores.';

export function dotenvPreloads(source: SourceFile): readonly Node[] {
  return [
    ...source.getImportDeclarations().filter(declaration => !declaration.isTypeOnly() && declaration.getModuleSpecifierValue() === 'dotenv/config'),
    ...source.getDescendantsOfKind(SyntaxKind.CallExpression).filter(call => {
      const module = call.getArguments()[0];
      return isModuleLoader(call) && isModuleLiteral(module) && module.getLiteralValue() === 'dotenv/config';
    }),
  ];
}

function origin(input: Node, seen = new Set<Node>()): Origin | undefined {
  const value = unwrap(input);
  if (seen.has(value)) return undefined;
  seen.add(value);
  if (Node.isCallExpression(value) && (isModuleLoader(value) || value.getExpression().getKind() === SyntaxKind.ImportKeyword)) {
    const argument = value.getArguments()[0];
    return isModuleLiteral(argument) ? { module: argument.getLiteralValue(),
      ...(isModuleLoader(value) ? {} : { uncertain: true }) } : undefined;
  }
  if (Node.isPropertyAccessExpression(value)) {
    const receiver = origin(value.getExpression(), seen);
    return receiver && (receiver.member === undefined ? { ...receiver, member: value.getName() } : { ...receiver, uncertain: true });
  }
  if (!Node.isIdentifier(value)) {
    for (const child of value.forEachChildAsArray()) {
      const receiver = origin(child, seen);
      if (receiver) return { ...receiver, uncertain: true };
    }
    return undefined;
  }
  const declarations = value.getSymbol()?.getDeclarations() ?? [];
  if (value.getText() === 'process' && declarations.every(declaration => declaration.getSourceFile().isDeclarationFile())) {
    return { module: 'node:process' };
  }
  for (const declaration of declarations) {
    const module = importEqualsModule(declaration);
    if (module && !(Node.isImportEqualsDeclaration(declaration) && declaration.isTypeOnly())) return { module: module.getLiteralValue() };
    if (Node.isImportClause(declaration) || Node.isNamespaceImport(declaration) || Node.isImportSpecifier(declaration)) {
      const owner = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration);
      if (!owner || owner.isTypeOnly() || Node.isImportSpecifier(declaration) && declaration.isTypeOnly()) continue;
      const member = Node.isImportSpecifier(declaration) ? declaration.getName() : 'default';
      return { module: owner.getModuleSpecifierValue(), ...(member === 'default' ? {} : { member }) };
    }
    if (Node.isVariableDeclaration(declaration)) {
      const initializer = declaration.getInitializer();
      const receiver = initializer ? origin(initializer, seen) : undefined;
      if (receiver) return bindingIsStable(declaration) ? receiver : { ...receiver, uncertain: true };
    }
    if (Node.isBindingElement(declaration)) {
      const owner = declaration.getFirstAncestorByKind(SyntaxKind.VariableDeclaration);
      const initializer = owner?.getInitializer();
      const receiver = initializer ? origin(initializer, seen) : undefined;
      const property = bindingKey(declaration);
      if (receiver) return receiver.member === undefined && property !== undefined &&
        declaration.getParent() === owner?.getNameNode() && !declaration.getDotDotDotToken() &&
        !declaration.getInitializer() && bindingIsStable(declaration)
        ? { ...receiver, member: property } : { ...receiver, uncertain: true };
    }
    if (!declaration.getSourceFile().isDeclarationFile() &&
      !Node.isVariableDeclaration(declaration) && !Node.isBindingElement(declaration) &&
      !Node.isImportEqualsDeclaration(declaration)) {
      const receiver = origin(declaration, seen);
      if (receiver) return { ...receiver, uncertain: true };
    }
  }
  return undefined;
}

function literalPaths(input: Node, root: string): Paths | undefined {
  const value = unwrap(input);
  const directory = dirname(value.getSourceFile().getFilePath());
  if (isModuleLiteral(value)) {
    const path = value.getLiteralValue();
    const rootPath = resolve(root, path);
    return { candidates: [...new Set([rootPath, resolve(directory, path)])], ...(isAbsolute(path) ? {} : { cwdCandidate: rootPath }) };
  }
  if (!Node.isCallExpression(value)) return undefined;
  const target = origin(value.getExpression());
  if (!target || target.uncertain || !['path', 'node:path'].includes(target.module) || !['join', 'resolve'].includes(target.member ?? '')) return undefined;
  const [base, ...parts] = value.getArguments();
  if (!base || !parts.every(isModuleLiteral)) return undefined;
  const anchor = unwrap(base);
  const sourceDirectory = Node.isIdentifier(anchor) && anchor.getText() === '__dirname' &&
    (anchor.getSymbol()?.getDeclarations() ?? []).every(declaration => declaration.getSourceFile().isDeclarationFile()) ||
    Node.isPropertyAccessExpression(anchor) && anchor.getName() === 'dirname' && anchor.getExpression().getText() === 'import.meta';
  const cwd = Node.isCallExpression(anchor) && anchor.getArguments().length === 0 ? origin(anchor.getExpression()) : undefined;
  const baseDirectory = sourceDirectory ? directory : !cwd?.uncertain && cwd?.member === 'cwd' && processModules.some(module => module === cwd.module) ? root : undefined;
  if (baseDirectory === undefined) return undefined;
  const segments = parts.map(part => part.getLiteralValue());
  const candidate = target.member === 'join' ? join(baseDirectory, ...segments) : resolve(baseDirectory, ...segments);
  return { candidates: [candidate], ...(sourceDirectory ? {} : { cwdCandidate: candidate }) };
}

/** Discover loader inputs before any rewrite; explicit files use dotenv scalar rules regardless of extension. */
export function environmentLoads(sources: readonly SourceFile[], context: ReadContext & { readonly failures: ReadFailures }): {
  readonly files: ReadonlySet<string>; readonly items: readonly Item[];
} {
  const files = new Set<string>();
  const items: Item[] = [];
  for (const source of sources) for (const call of [
    ...source.getDescendantsOfKind(SyntaxKind.CallExpression),
    ...dotenvPreloads(source).filter(Node.isImportDeclaration),
  ]) {
    const module = Node.isCallExpression(call) ? call.getArguments()[0] : undefined;
    const sideEffect = Node.isImportDeclaration(call) || isModuleLoader(call) &&
      isModuleLiteral(module) && module.getLiteralValue() === 'dotenv/config';
    const target: Origin | undefined = sideEffect ? { module: 'dotenv', member: 'config' } :
      Node.isCallExpression(call) ? origin(call.getExpression()) : undefined;
    if (!target) continue;
    const review = (): void => { items.push({ ...location(call, context.root), kind: 'env-api-url',
      message: DOTENV_LOAD_REVIEW }); };
    const nodeModule = processModules.some(module => module === target.module);
    const dotenvModule = dotenvModules.some(module => module === target.module);
    if (target.uncertain && (dotenvModule || nodeModule && (target.member === undefined || target.member === 'loadEnvFile'))) { review(); continue; }
    const nodeLoader = nodeModule && target.member === 'loadEnvFile';
    if (!nodeLoader && !(dotenvModule && target.member === 'config')) continue;
    const argument = !sideEffect && Node.isCallExpression(call) ? call.getArguments()[0] : undefined;
    let inputs: readonly Node[] = [];
    let complete = true;
    if (nodeLoader) inputs = argument ? [argument] : [];
    else if (argument) {
      const options = unwrap(argument);
      if (!Node.isObjectLiteralExpression(options)) { review(); continue; }
      // Spreads/computed keys can replace or supply path; never certify those options.
      complete = !options.getProperties().some(property => Node.isSpreadAssignment(property) ||
        Node.isComputedPropertyName(property.getNameNode()) || property.getName() === 'path' && !Node.isPropertyAssignment(property));
      inputs = options.getProperties().filter(Node.isPropertyAssignment).filter(property => {
        const key = property.getNameNode();
        return (isModuleLiteral(key) ? key.getLiteralValue() : key.getText()) === 'path';
      })
        .map(property => property.getInitializerOrThrow());
    }
    const paths = inputs.flatMap(input => {
      const value = unwrap(input);
      return Node.isArrayLiteralExpression(value) ? value.getElements() : [value];
    });
    const resolved = paths.length ? paths.map(input => literalPaths(input, context.root)) :
      [{ candidates: [resolve(context.root, target.module === 'dotenv-flow' ? '.' : '.env')],
        cwdCandidate: resolve(context.root, target.module === 'dotenv-flow' ? '.' : '.env') }];
    for (const path of resolved) {
      if (!path) { complete = false; continue; }
      let scanned = false;
      let cwdScanned = path.cwdCandidate === undefined;
      for (const candidate of path.candidates) {
        if (!existsSync(candidate)) continue;
        if (target.module === 'dotenv-flow') {
          const names = context.failures.read(candidate, () => readdirSync(candidate)) ?? [];
          for (const name of names) if (/^\.env(?:\.|$)/.test(name)) {
            files.add(join(candidate, name)); scanned = true;
            if (candidate === path.cwdCandidate) cwdScanned = true;
          }
        } else {
          const stat = context.failures.read(candidate, () => statSync(candidate));
          if (stat?.isFile()) {
            files.add(candidate); scanned = true;
            if (candidate === path.cwdCandidate) cwdScanned = true;
          }
        }
      }
      complete &&= scanned && cwdScanned;
    }
    if (!complete) review();
  }
  return { files, items };
}
