/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Item, Mapping } from './model.js';
import { location, unwrap } from './edits.js';
import { isObjectAssign } from './intrinsics.js';
import { bindingIsReassigned, bindingIsStable } from './stable-bindings.js';
import { isCloudApiUrl } from './cloud-api-url.js';
import { mappingDeleteCutoffs } from './mapping-deletions.js';
import { isProcessSource, mayWriteEnvironment } from './environment-writes.js';

export type EnvironmentContext = { readonly mapping: Mapping; readonly dropApiUrl: boolean };
export const ENV_CUSTOM_URL = 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x. If it points at Daytona Cloud, rerun with --drop-api-url.';
export function envUrlItems(position: Pick<Item, 'file' | 'line' | 'column'>, setting: {
  readonly name: string | undefined; readonly literal: string | undefined;
}, context: EnvironmentContext): readonly Item[] {
  const custom = setting.literal !== undefined && !isCloudApiUrl(setting.literal, context.mapping) && !context.dropApiUrl;
  const review: Item = { ...position, kind: 'env-api-url',
    message: setting.name === undefined
      ? 'an environment write may set DAYTONA_API_URL or DAYTONA_SERVER_URL; check that it does not point at a custom API'
      : `${setting.name} is ignored by SDK 2.x: .v0 targets Daytona Cloud, and .v2 reads only DAYTONA_V2_API_URL, for test environments. Review this setting.` };
  return custom ? [review, { ...position, kind: 'custom-api-url', message: ENV_CUSTOM_URL }] : [review];
}

export function environmentLiteral(text: string): string | undefined {
  const value = text.trim();
  if (value.startsWith('#')) return '';
  if (/\$|\{\{|^[|>]/.test(value)) return undefined;
  const quoted = value.match(/^(['"])(.*?)\1\s*(?:#.*)?$/);
  if (quoted) return quoted[2];
  if (/^['"]/.test(value)) return undefined;
  return value.replace(/\s+#.*$/, '').trim();
}

function environmentKey(node: Node): string | undefined {
  if (Node.isPropertyAccessExpression(node)) return node.getName();
  if (Node.isElementAccessExpression(node)) {
    const key = node.getArgumentExpression();
    return key ? constantKey(key) : undefined;
  }
  return undefined;
}

export function constantKey(node: Node, seen: Set<Node> = new Set()): string | undefined {
  const value = unwrap(node);
  if (Node.isStringLiteral(value) || Node.isNoSubstitutionTemplateLiteral(value)) return value.getLiteralValue();
  if (Node.isNumericLiteral(value)) return String(value.getLiteralValue());
  if (Node.isBinaryExpression(value) && value.getOperatorToken().getKind() === SyntaxKind.PlusToken) {
    const left = constantKey(value.getLeft(), seen);
    const right = constantKey(value.getRight(), seen);
    return left !== undefined && right !== undefined ? left + right : undefined;
  }
  if (Node.isTemplateExpression(value)) {
    let result = value.getHead().getLiteralText();
    for (const span of value.getTemplateSpans()) {
      const expression = constantKey(span.getExpression(), seen);
      if (expression === undefined) return undefined;
      result += expression + span.getLiteral().getLiteralText();
    }
    return result;
  }
  if (!Node.isIdentifier(value) || seen.has(value)) return undefined;
  seen.add(value);
  const declarations = value.getSymbol()?.getDeclarations() ?? [];
  if (declarations.length !== 1) return undefined;
  const declaration = declarations[0];
  if (!Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return undefined;
  const initializer = declaration.getInitializer();
  return initializer ? constantKey(initializer, seen) : undefined;
}

function environmentDefinitions(node: Node): readonly Node[] {
  const declarations = node.getSymbol()?.getDeclarations() ?? [];
  if (!straightLineEnvironment(node)) return declarations;
  const bindings = declarations.filter(declaration => (Node.isVariableDeclaration(declaration) || Node.isBindingElement(declaration)) &&
    declaration.getSourceFile() === node.getSourceFile() && declaration.getStart() < node.getStart());
  const latest = bindings.sort((left, right) => right.getStart() - left.getStart())[0];
  return declarations.filter(declaration => !(Node.isVariableDeclaration(declaration) || Node.isBindingElement(declaration)) || declaration === latest);
}

function straightLineEnvironment(node: Node): boolean {
  const declarations = node.getSymbol()?.getDeclarations() ?? [];
  const container = node.getFirstAncestor(Node.isStatement)?.getParent();
  return declarations.length === 1 && (Node.isSourceFile(container) || Node.isBlock(container)) &&
    declarations.every(declaration =>
      !(Node.isVariableDeclaration(declaration) || Node.isBindingElement(declaration)) ||
      declaration.getFirstAncestor(Node.isVariableStatement)?.getParent() === container && declaration.getStart() < node.getStart());
}

function environmentReassigned(declaration: Node, reference: Node): boolean {
  return straightLineEnvironment(reference) && bindingIsReassigned(declaration, reference);
}

function isProcessEnv(node: Node, seen: Set<Node> = new Set()): boolean {
  const env = unwrap(node);
  if (seen.has(env)) return false;
  seen.add(env);
  if (Node.isIdentifier(env)) return environmentDefinitions(env).some(declaration => {
    if (Node.isImportSpecifier(declaration) && declaration.getName() === 'env' && !declaration.isTypeOnly()) {
      const owner = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration);
      return owner !== undefined && ['process', 'node:process'].includes(owner.getModuleSpecifierValue()) && !owner.isTypeOnly();
    }
    if (Node.isBindingElement(declaration)) {
      const pattern = declaration.getParent();
      const owner = pattern.getParent();
      if (!Node.isObjectBindingPattern(pattern) || !Node.isVariableDeclaration(owner) ||
        declaration.getDotDotDotToken() || environmentReassigned(declaration, env)) return false;
      const property = declaration.getPropertyNameNode() ?? declaration.getNameNode();
      const key = Node.isComputedPropertyName(property) ? constantKey(property.getExpression()) :
        Node.isStringLiteral(property) ? property.getLiteralValue() : property.getText();
      const initializer = owner.getInitializer();
      return key === 'env' && initializer !== undefined && isProcess(initializer);
    }
    if (!Node.isVariableDeclaration(declaration) || environmentReassigned(declaration, env)) return false;
    const initializer = declaration.getInitializer();
    return initializer !== undefined && isProcessEnv(initializer, seen);
  });
  if (!(Node.isPropertyAccessExpression(env) || Node.isElementAccessExpression(env)) || environmentKey(env) !== 'env') return false;
  return isProcess(env.getExpression());
}

function isProcess(node: Node, seen: Set<Node> = new Set()): boolean {
  const process = unwrap(node);
  if (isProcessSource(process)) return true;
  if (!Node.isIdentifier(process) || seen.has(process)) return false;
  seen.add(process);
  const declarations = environmentDefinitions(process);
  if (declarations.some(declaration => Node.isVariableDeclaration(declaration) && !environmentReassigned(declaration, process) &&
    declaration.getInitializer() !== undefined && isProcess(declaration.getInitializerOrThrow(), seen))) return true;
  const nodeImport = declarations.some(declaration => {
    if (!Node.isImportClause(declaration) && !Node.isNamespaceImport(declaration) &&
      !(Node.isImportSpecifier(declaration) && declaration.getName() === 'default' && !declaration.isTypeOnly())) return false;
    const owner = declaration.getFirstAncestorByKind(SyntaxKind.ImportDeclaration);
    return owner !== undefined && ['process', 'node:process'].includes(owner.getModuleSpecifierValue()) && !owner.isTypeOnly();
  });
  return nodeImport || (process.getText() === 'process' && (process.getSymbol()?.getDeclarations() ?? []).every(declaration => {
    const parent = declaration.getParent();
    // JS property writes can synthesize an implicit global Identifier declaration.
    return declaration.getSourceFile().isDeclarationFile() || Node.isIdentifier(declaration) &&
      (Node.isPropertyAccessExpression(parent) || Node.isElementAccessExpression(parent)) && parent.getExpression() === declaration;
  }));
}

export function sourceEnvUrls(source: SourceFile, root: string, context: EnvironmentContext): readonly Item[] {
  if (!mayWriteEnvironment(source.compilerNode)) return [];
  const items: Item[] = [];
  const record = (node: Node, name: string | undefined, input: Node): void => {
    if (name !== undefined && name !== 'DAYTONA_API_URL' && name !== 'DAYTONA_SERVER_URL') return;
    const value = unwrap(input);
    // An unknown key may name an unrelated setting: report it, never refuse it.
    const literal = name !== undefined && (Node.isStringLiteral(value) || Node.isNoSubstitutionTemplateLiteral(value)) ? value.getLiteralValue() : undefined;
    if (literal !== '') items.push(...envUrlItems(location(node, root), { name, literal }, context));
  };
  const mappingWrites = (input: Node, seen: Set<Node> = new Set(), before: Node = input): void => {
    const value = unwrap(input);
    if (seen.has(value)) return;
    seen.add(value);
    const deletions = mappingDeleteCutoffs(before, environmentKey);
    const recordMapping = (node: Node, name: string | undefined, input: Node): void => {
      const cutoff = name === undefined ? undefined : deletions.get(name);
      if (cutoff !== undefined && node.getStart() < cutoff && !node.getFirstAncestor(Node.isFunctionLikeDeclaration)) return;
      record(node, name, input);
    };
    if (Node.isIdentifier(value)) {
      const declarations = value.getSymbol()?.getDeclarations() ?? [];
      if (!declarations.some(Node.isVariableDeclaration)) record(value, undefined, value);
      for (const declaration of declarations) {
        if (!Node.isVariableDeclaration(declaration)) continue;
        if (bindingIsStable(declaration)) {
          const initializer = declaration.getInitializer();
          if (initializer) mappingWrites(initializer, seen, before);
          else record(value, undefined, value);
        } else record(value, undefined, value);
        const name = declaration.getNameNode();
        if (!Node.isIdentifier(name)) continue;
        // TypeScript can report a whole statement for a dynamic element write.
        const references = name.findReferencesAsNodes().flatMap(reference => Node.isIdentifier(reference)
          ? [reference] : reference.getDescendantsOfKind(SyntaxKind.Identifier).filter(identifier => identifier.getSymbol() === name.getSymbol()));
        for (const reference of references) {
          // Only top-level, single-pass copies establish a temporal cutoff.
          // Functions and loops can observe later writes on another invocation.
          if (reference.getSourceFile() === before.getSourceFile() && reference.getStart() > before.getStart() &&
            !reference.getFirstAncestor(Node.isFunctionLikeDeclaration) && !before.getFirstAncestor(Node.isFunctionLikeDeclaration) &&
            !before.getFirstAncestor(node => Node.isForStatement(node) || Node.isForOfStatement(node) || Node.isForInStatement(node) ||
              Node.isWhileStatement(node) || Node.isDoStatement(node))) continue;
          let use: Node = reference;
          while (Node.isParenthesizedExpression(use.getParent())) use = use.getParentOrThrow();
          const parent = use.getParent();
          if (Node.isVariableDeclaration(parent) && parent.getInitializer() && unwrap(parent.getInitializerOrThrow()) === reference) {
            mappingWrites(parent.getNameNode(), seen, before);
          }
          if (!(Node.isPropertyAccessExpression(parent) || Node.isElementAccessExpression(parent)) || parent.getExpression() !== use) continue;
          const write = parent.getParent();
          if (!Node.isBinaryExpression(write) || write.getLeft() !== parent) continue;
          const operator = write.getOperatorToken().getKind();
          if (operator >= SyntaxKind.FirstAssignment && operator <= SyntaxKind.LastAssignment) {
            recordMapping(write, environmentKey(parent), write.getRight());
          }
        }
      }
    }
    if (Node.isIdentifier(value)) return;
    if (!Node.isObjectLiteralExpression(value)) {
      record(value, undefined, value);
      return;
    }
    for (const property of value.getProperties()) {
      if (Node.isSpreadAssignment(property)) mappingWrites(property.getExpression(), seen, before);
      if (Node.isPropertyAssignment(property)) {
        const key = property.getNameNode();
        const nameNode = Node.isComputedPropertyName(key) ? unwrap(key.getExpression()) : key;
        const name = Node.isComputedPropertyName(key) ? constantKey(nameNode) :
          Node.isStringLiteral(nameNode) || Node.isNoSubstitutionTemplateLiteral(nameNode) ? nameNode.getLiteralValue() : nameNode.getText();
        recordMapping(property, name, property.getInitializerOrThrow());
      }
      if (Node.isShorthandPropertyAssignment(property)) recordMapping(property, property.getName(), property.getNameNode());
      if (Node.isGetAccessorDeclaration(property) || Node.isSetAccessorDeclaration(property) || Node.isMethodDeclaration(property)) {
        const key = property.getNameNode();
        record(property, Node.isComputedPropertyName(key) ? constantKey(key.getExpression()) : property.getName(), property);
      }
    }
  };
  // Destructuring and loop targets are writes too, but their values cannot be
  // inferred from the complete RHS. Keep these report-only rather than guess.
  const writeTarget = (input: Node, value: Node): void => {
    const target = unwrap(input);
    if (isProcessEnv(target)) mappingWrites(value);
    else if ((Node.isPropertyAccessExpression(target) || Node.isElementAccessExpression(target)) && isProcessEnv(target.getExpression())) {
      record(target, environmentKey(target), value);
    } else if (Node.isArrayLiteralExpression(target)) {
      for (const element of target.getElements()) writeTarget(element, value);
    } else if (Node.isObjectLiteralExpression(target)) {
      for (const property of target.getProperties()) {
        if (Node.isPropertyAssignment(property)) writeTarget(property.getInitializerOrThrow(), value);
        else if (Node.isSpreadAssignment(property)) writeTarget(property.getExpression(), value);
      }
    } else if (Node.isSpreadElement(target)) writeTarget(target.getExpression(), value);
    else if (Node.isBinaryExpression(target) && target.getOperatorToken().getKind() === SyntaxKind.EqualsToken) writeTarget(target.getLeft(), target);
  };
  source.forEachDescendant(node => {
    if (Node.isBinaryExpression(node)) {
      const operator = node.getOperatorToken().getKind();
      if (operator >= SyntaxKind.FirstAssignment && operator <= SyntaxKind.LastAssignment) writeTarget(node.getLeft(), node.getRight());
    }
    if (Node.isForInStatement(node) || Node.isForOfStatement(node)) writeTarget(node.getInitializer(), node);
    if (!Node.isCallExpression(node)) return;
    const call = node;
    const [target, ...sources] = call.getArguments();
    if (target && isObjectAssign(call) && isProcessEnv(target)) for (const input of sources) mappingWrites(input);
    const access = unwrap(call.getExpression());
    if (!target || !isProcessEnv(target) || !Node.isPropertyAccessExpression(access)) return;
    const receiver = unwrap(access.getExpression());
    if (!Node.isIdentifier(receiver)) return;
    const intrinsic = receiver.getText();
    if (!(intrinsic === 'Reflect' && access.getName() === 'set' || intrinsic === 'Object' && access.getName() === 'defineProperty')) return;
    const program = source.getProject().getProgram().compilerObject;
    const declarations = receiver.getSymbol()?.getDeclarations() ?? [];
    if (declarations.length === 0 || !declarations.every(declaration => program.isSourceFileDefaultLibrary(declaration.getSourceFile().compilerNode))) return;
    const [key, argument] = sources;
    if (!key || !argument) return;
    const descriptor = unwrap(argument);
    const property = Node.isObjectLiteralExpression(descriptor) ? descriptor.getProperty('value') : undefined;
    const value = intrinsic === 'Reflect' ? argument : Node.isPropertyAssignment(property) ? property.getInitializerOrThrow() : call;
    record(call, constantKey(key), value);
  });
  return items;
}
