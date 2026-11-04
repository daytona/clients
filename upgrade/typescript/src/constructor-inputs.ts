/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { ConstructorDeclaration } from 'ts-morph';
import { declarations } from './bindings.js';
import { bindingKey, propertyKey } from './binding-key.js';
import { unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';

function projectConstructor(expression: Node, seen: ReadonlySet<Node> = new Set()): ConstructorDeclaration | undefined {
  const target = unwrap(expression);
  if (seen.has(target)) return undefined;
  const path = new Set(seen).add(target);
  const candidates = Node.isClassDeclaration(target) || Node.isClassExpression(target) ? [target] : declarations(target);
  if (candidates.length !== 1) return undefined;
  const declaration = candidates[0];
  if (Node.isClassDeclaration(declaration) || Node.isClassExpression(declaration)) {
    if (declaration.getSourceFile().isDeclarationFile()) return undefined;
    const constructors = declaration.getConstructors();
    if (constructors.length) return constructors.find(constructor => constructor.getBody() !== undefined);
    const base = declaration.getExtends()?.getExpression();
    return base && projectConstructor(base, path);
  }
  if (!Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return undefined;
  const init = declaration.getInitializer();
  return init && projectConstructor(init, path);
}

export function propertyInput(input: Node, key: string): readonly Node[] {
  if (!Node.isObjectLiteralExpression(input)) return [];
  const properties = input.getProperties();
  const index = properties.findIndex(entry => propertyKey(entry) === key);
  const property = properties[index];
  if (!property) return [];
  // Only earlier entries are shadowed by the explicit property. Later unknown
  // keys or duplicate definitions (escaped spellings included) invalidate its
  // scalar URL evidence.
  if (properties.slice(index + 1).some(later => {
    const laterKey = propertyKey(later);
    return laterKey === undefined || laterKey === key;
  })) return [];
  if (Node.isPropertyAssignment(property)) {
    const init = property.getInitializer();
    return init ? [init] : [];
  }
  return Node.isShorthandPropertyAssignment(property) ? [property.getNameNode()] : [];
}

// The config collector keeps the unresolved parameter alongside this evidence.
// URL fields may use fully resolved scalar inputs; unsupported paths stay unknown.
export function constructorInputs(declaration: Node, seen: ReadonlySet<Node> = new Set()): readonly Node[] {
  const parameter = Node.isParameterDeclaration(declaration) ? declaration :
    Node.isBindingElement(declaration) ? declaration.getFirstAncestorByKind(SyntaxKind.Parameter) : undefined;
  const owner = parameter?.getParent();
  if (!parameter || !Node.isConstructorDeclaration(owner) || parameter.getDotDotDotToken()) return [];
  const path = new Set(seen).add(declaration);
  const index = owner.getParameters().indexOf(parameter);
  const inputs: Node[] = [];
  const fallback = parameter.getInitializer();
  if (fallback) inputs.push(...constructorValues(fallback, path));
  for (const source of owner.getProject().getSourceFiles()) {
    if (source.isDeclarationFile()) continue;
    const calls = [...source.getDescendantsOfKind(SyntaxKind.NewExpression),
      ...source.getDescendantsOfKind(SyntaxKind.CallExpression).filter(call => call.getExpression().getKind() === SyntaxKind.SuperKeyword)];
    for (const call of calls) {
      const enclosing = call.getFirstAncestorByKind(SyntaxKind.Constructor)?.getParent();
      const target = Node.isNewExpression(call) ? call.getExpression() :
        Node.isClassDeclaration(enclosing) || Node.isClassExpression(enclosing) ? enclosing.getExtends()?.getExpression() : undefined;
      if (!target || projectConstructor(target) !== owner) continue;
      const args = call.getArguments();
      if (args.slice(0, index + 1).some(Node.isSpreadElement)) { inputs.push(parameter); continue; }
      const argument = args[index];
      if (argument) inputs.push(...constructorValues(argument, path));
      else if (!fallback) inputs.push(parameter);
    }
  }
  if (!Node.isBindingElement(declaration)) return inputs;
  if (declaration.getParent() !== parameter.getNameNode() || declaration.getDotDotDotToken()) return [];
  const pattern = declaration.getParent();
  const selected = inputs.flatMap(input => {
    if (Node.isObjectBindingPattern(pattern)) {
      const key = bindingKey(declaration);
      const properties = key === undefined ? [] : propertyInput(input, key);
      return properties.length ? properties : [declaration];
    }
    if (!Node.isArrayBindingPattern(pattern) || !Node.isArrayLiteralExpression(input) || input.getElements().some(Node.isSpreadElement)) return [declaration];
    const element = input.getElements()[pattern.getElements().indexOf(declaration)];
    return element ? [element] : [declaration];
  });
  const defaultValue = declaration.getInitializer();
  return defaultValue ? [...selected, defaultValue] : selected;
}

export function constructorValues(input: Node, seen: ReadonlySet<Node> = new Set()): readonly Node[] {
  const value = unwrap(input);
  if (seen.has(value)) return [value];
  const path = new Set(seen).add(value);
  if (Node.isIdentifier(value) || Node.isParameterDeclaration(value) || Node.isBindingElement(value)) {
    const parent = value.getParent();
    const definitions = Node.isParameterDeclaration(value) || Node.isBindingElement(value) ? [value] :
      Node.isShorthandPropertyAssignment(parent) ? parent.getValueSymbol()?.getDeclarations() ?? [] : declarations(value);
    const resolved = definitions.flatMap(definition => {
      if (Node.isParameterDeclaration(definition) || Node.isBindingElement(definition)) {
        const arguments_ = constructorInputs(definition, path).flatMap(argument => constructorValues(argument, path));
        return bindingIsStable(definition) && arguments_.length ? arguments_ : [value, ...arguments_];
      }
      if (!Node.isVariableDeclaration(definition) || !bindingIsStable(definition)) return [value];
      const init = definition.getInitializer();
      return init ? constructorValues(init, path) : [value];
    });
    return resolved.length ? resolved : [value];
  }
  if (Node.isPropertyAccessExpression(value) || Node.isElementAccessExpression(value)) {
    const key = Node.isElementAccessExpression(value) ? value.getArgumentExpression() : undefined;
    const name = Node.isPropertyAccessExpression(value) ? value.getName() : Node.isStringLiteral(key) ? key.getLiteralValue() : undefined;
    if (name !== undefined) return [value, ...constructorValues(value.getExpression(), path)
      .flatMap(receiver => propertyInput(receiver, name)).flatMap(property => constructorValues(property, path))];
  }
  return [value];
}
