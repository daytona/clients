/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, VariableDeclarationKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { unwrap } from './edits.js';
import { objectAssignReceiver } from './intrinsics.js';
import { isMemberAccess, memberName } from './member-access.js';
import { bindingKey } from './binding-key.js';

const stability = new WeakMap<Node, boolean>();
const writeIndexes = new WeakMap<SourceFile, ReadonlyMap<Node, readonly Node[]>>();

function literalKey(node: Node | undefined): string | undefined {
  const value = node && unwrap(Node.isComputedPropertyName(node) ? node.getExpression() : node);
  return Node.isStringLiteral(value) || Node.isNoSubstitutionTemplateLiteral(value) ? value.getLiteralValue() : undefined;
}

function memberWritten(receiver: Node, name: string, writes: ReadonlyMap<Node, readonly Node[]>): boolean {
  const property = receiver.getType().getProperty(name);
  if ((property?.getDeclarations() ?? []).some(declaration => (writes.get(declaration) ?? []).length > 0)) return true;
  // Writes recorded on the access itself (untyped or widened receivers).
  const text = `${unwrap(receiver).getText()}.${name}`;
  for (const recorded of writes.values()) {
    if (recorded.some(write => Node.isBinaryExpression(write) && unwrap(write.getLeft()).getText() === text)) return true;
  }
  return false;
}

// Resolve syntax against the completed rebinding index, without recursively
// invoking bindingIsStable while that index is still being constructed.
function mutatorPath(node: Node, writes: ReadonlyMap<Node, readonly Node[]>, seen: Set<Node> = new Set()): string | undefined {
  const value = unwrap(node);
  if (seen.has(value)) return undefined;
  seen.add(value);
  if (isMemberAccess(value)) {
    const receiver = mutatorPath(value.getExpression(), writes, seen);
    const name = memberName(value);
    return receiver && name ? receiver === 'globalThis' ? name : `${receiver}.${name}` : undefined;
  }
  if (!Node.isIdentifier(value)) return undefined;
  const declarations = value.getSymbol()?.getDeclarations() ?? [];
  for (const declaration of declarations) {
    if (value.getProject().getProgram().compilerObject.isSourceFileDefaultLibrary(declaration.getSourceFile().compilerNode)) continue;
    if (!Node.isVariableDeclaration(declaration) && !Node.isBindingElement(declaration)) continue;
    const variable = Node.isVariableDeclaration(declaration) ? declaration : declaration.getFirstAncestorByKind(SyntaxKind.VariableDeclaration);
    if (!variable || variable.getVariableStatement()?.getDeclarationKind() !== VariableDeclarationKind.Const ||
        (writes.get(declaration) ?? []).some(write => !Node.isCallExpression(write))) return undefined;
    const init = variable.getInitializer();
    if (!init) return undefined;
    const path = mutatorPath(init, writes, seen);
    if (Node.isVariableDeclaration(declaration)) return path;
    if (declaration.getParent() !== variable.getNameNode() || declaration.getDotDotDotToken() || declaration.getInitializer()) return undefined;
    const key = bindingKey(declaration);
    return path && key ? path === 'globalThis' ? key : `${path}.${key}` : undefined;
  }
  return ['Reflect', 'Object', 'globalThis'].includes(value.getText()) ? value.getText() : undefined;
}

function writesIn(source: SourceFile): ReadonlyMap<Node, readonly Node[]> {
  const cached = writeIndexes.get(source);
  if (cached) return cached;
  const writes = new Map<Node, Node[]>();
  const record = (target: Node, write: Node): void => {
    if (Node.isObjectLiteralExpression(target) || Node.isArrayLiteralExpression(target)) {
      for (const node of target.getDescendants()) {
        if (Node.isShorthandPropertyAssignment(node)) {
          const symbol = node.getValueSymbol();
          for (const declaration of symbol?.getDeclarations() ?? []) writes.set(declaration, [...(writes.get(declaration) ?? []), write]);
        } else if (Node.isIdentifier(node)) record(node, write);
      }
      return;
    }
    if (!(Node.isIdentifier(target) || Node.isPropertyAccessExpression(target) || Node.isElementAccessExpression(target))) return;
    const name = memberName(target);
    // JS assignment symbols can declare just the LHS access; its receiver type
    // still identifies the original object-literal property read elsewhere.
    const property = isMemberAccess(target) && name !== undefined ? target.getExpression().getType().getProperty(name) : undefined;
    for (const symbol of [target.getSymbol(), property]) {
      for (const declaration of (symbol?.getAliasedSymbol() ?? symbol)?.getDeclarations() ?? []) writes.set(declaration, [...(writes.get(declaration) ?? []), write]);
    }
  };
  const nodes = source.getDescendants();
  for (const node of nodes) {
    // Unknown/shadowed assign calls may still mutate their target; only config
    // rewriting requires proof that the receiver is the global intrinsic.
    if (Node.isCallExpression(node) && objectAssignReceiver(node)) {
      const argument = node.getArguments()[0];
      if (!argument) continue;
      const target = unwrap(argument);
      if (!Node.isObjectLiteralExpression(target) && !Node.isArrayLiteralExpression(target)) record(target, node);
    } else if (Node.isBinaryExpression(node)) {
      const operator = node.getOperatorToken().getKind();
      if (operator >= SyntaxKind.FirstAssignment && operator <= SyntaxKind.LastAssignment) record(node.getLeft(), node);
    } else if (Node.isPrefixUnaryExpression(node) || Node.isPostfixUnaryExpression(node)) {
      const operator = node.getOperatorToken();
      if (operator === SyntaxKind.PlusPlusToken || operator === SyntaxKind.MinusMinusToken) record(node.getOperand(), node);
    } else if (Node.isForOfStatement(node) || Node.isForInStatement(node)) record(node.getInitializer(), node);
  }
  // Resolve from syntax, not an alias's apparent type, which may hide fields.
  // Use the completed rebinding index; calling bindingIsStable here recurses.
  const resolveTarget = (argument: Node): Node | undefined => {
    let target = unwrap(argument);
    const seen = new Set<Node>();
    while (Node.isIdentifier(target) && !seen.has(target)) {
      seen.add(target);
      // In JS the compiler also declares an identifier passed as the target of
      // Object.defineProperty. That is a mutation, not a second binding.
      const declarations = (target.getSymbol()?.getDeclarations() ?? []).filter(declaration => {
        const parent = declaration.getParent();
        return !(Node.isIdentifier(declaration) && Node.isCallExpression(parent) &&
          memberName(unwrap(parent.getExpression())) === 'defineProperty' && parent.getArguments()[0] === declaration);
      });
      const declaration = declarations[0];
      if (declarations.length !== 1 || !Node.isVariableDeclaration(declaration) ||
          (writes.get(declaration) ?? []).some(write => !Node.isCallExpression(write))) return undefined;
      const init = declaration.getInitializer();
      if (!init) return undefined;
      target = unwrap(init);
    }
    return Node.isObjectLiteralExpression(target) || Node.isArrayLiteralExpression(target) ||
      Node.isNewExpression(target) || Node.isThisExpression(target) ? target : undefined;
  };
  for (const node of nodes) {
    if (!Node.isCallExpression(node)) continue;
    const path = mutatorPath(node.getExpression(), writes);
    const method = path?.split('.').at(-1);
    const reflective = path !== undefined && /^(?:Reflect\.(?:set|defineProperty)|Object\.(?:defineProperty|defineProperties))$/.test(path);
    const assign = path === 'Object.assign' || objectAssignReceiver(node) !== undefined;
    const argument = node.getArguments()[0];
    if ((!reflective && !assign) || !argument) continue;
    if (assign && !Node.isObjectLiteralExpression(unwrap(argument)) && !Node.isArrayLiteralExpression(unwrap(argument))) record(unwrap(argument), node);
    const target = resolveTarget(argument);
    if (target) {
      // A known object may lose any of its properties through a mutator.
      for (const property of target.getType().getProperties()) {
        for (const declaration of property.getDeclarations()) writes.set(declaration, [...(writes.get(declaration) ?? []), node]);
      }
      continue;
    }
    const key = node.getArguments()[1];
    const keys = new Set<string>();
    let knownKeys = true;
    if (assign || method === 'defineProperties') {
      const inputs = assign ? node.getArguments().slice(1) : key ? [key] : [];
      for (const input of inputs) {
        const object = unwrap(input);
        if (!Node.isObjectLiteralExpression(object)) { knownKeys = false; break; }
        for (const property of object.getProperties()) {
          if (Node.isSpreadAssignment(property)) { knownKeys = false; break; }
          const nameNode = property.getNameNode();
          const name = Node.isIdentifier(nameNode) ? nameNode.getText() : literalKey(nameNode);
          if (name === undefined) { knownKeys = false; break; }
          keys.add(name);
        }
      }
    } else {
      const name = literalKey(key);
      if (name === undefined) knownKeys = false;
      else keys.add(name);
    }
    // A reassigned mutator (`R.set = custom`) may write any key, whatever the call passes.
    const callee = unwrap(node.getExpression());
    if (isMemberAccess(callee) && memberWritten(callee.getExpression(), memberName(callee) ?? '', writes)) knownKeys = false;
    // An opaque target may alias any object in this file. Unknown keys can
    // replace every member; literal keys only invalidate that member name.
    for (const candidate of nodes) {
      if (isMemberAccess(candidate)) {
        if (!knownKeys || keys.has(memberName(candidate) ?? '')) record(candidate, node);
      } else if (Node.isPropertyAssignment(candidate) || Node.isShorthandPropertyAssignment(candidate) || Node.isPropertyDeclaration(candidate)) {
        const nameNode = candidate.getNameNode();
        const name = Node.isIdentifier(nameNode) ? nameNode.getText() : literalKey(nameNode);
        if (!knownKeys || name !== undefined && keys.has(name)) writes.set(candidate, [...(writes.get(candidate) ?? []), node]);
      }
    }
  }
  writeIndexes.set(source, writes);
  return writes;
}

export function bindingIsStable(declaration: Node): boolean {
  const cached = stability.get(declaration);
  if (cached !== undefined) return cached;
  const symbol = Node.isBindingElement(declaration) || Node.isVariableDeclaration(declaration) ||
    Node.isParameterDeclaration(declaration) || Node.isPropertyDeclaration(declaration) || Node.isPropertyAssignment(declaration) ||
    Node.isShorthandPropertyAssignment(declaration) || Node.isGetAccessorDeclaration(declaration) ||
    Node.isSetAccessorDeclaration(declaration) ? declaration.getNameNode().getSymbol() :
    Node.isBinaryExpression(declaration) ? declaration.getLeft().getSymbol() :
    isMemberAccess(declaration) ? declaration.getSymbol() : undefined;
  if (!symbol) return true;
  if (symbol.getDeclarations().filter(Node.isVariableDeclaration).length > 1) return false;
  const source = declaration.getSourceFile();
  const sources = source.getProject().getSourceFiles().filter(file => !file.isDeclarationFile());
  const writes = [...new Set(sources.flatMap(file => symbol.getDeclarations().flatMap(target => writesIn(file).get(target) ?? [])))].filter(node =>
    node !== declaration && !(Node.isBinaryExpression(node) && node.getLeft() === declaration));
  const stable = writes.length === 0 || Node.isPropertyDeclaration(declaration) && !declaration.getInitializer() && writes.length === 1;
  stability.set(declaration, stable);
  return stable;
}

// Share the completed alias/rebinding analysis with receiver-side mutation fences.
export function mutationPath(call: import('ts-morph').CallExpression): string | undefined {
  return mutatorPath(call.getExpression(), writesIn(call.getSourceFile()));
}

// Config objects may be mutated with Object.assign without rebinding the const.
// Namespace/import provenance uses the stricter bindingIsStable check above.
export function bindingIsReassigned(declaration: Node, before?: Node): boolean {
  if (!Node.isVariableDeclaration(declaration) && !Node.isBindingElement(declaration)) return false;
  const symbol = declaration.getNameNode().getSymbol();
  const definitions = symbol?.getDeclarations() ?? [];
  const relevant = (node: Node): boolean => before === undefined ||
    node.getSourceFile() !== before.getSourceFile() || node.getStart() >= declaration.getStart() && node.getStart() < before.getStart();
  return definitions.filter(definition => (Node.isVariableDeclaration(definition) || Node.isBindingElement(definition)) && relevant(definition)).length > 1 || definitions.some(definition =>
    (writesIn(declaration.getSourceFile()).get(definition) ?? []).some(write => !Node.isCallExpression(write) && relevant(write)));
}
