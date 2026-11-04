/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, VariableDeclarationKind, ts } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { declarations, inheritsClientConstructor, isClientConstructor } from './bindings.js';
import { unwrap } from './edits.js';
import { isConfigAssign, localConfigHelper } from './config-url-fallback.js';
import { configValue, containsConfig } from './config-references.js';
import { isConfigReadOnlyCall } from './config-readonly.js';
import { configDeclarations } from './config-bindings.js';
import { bindingIsReassigned } from './stable-bindings.js';
import type { Mapping } from './model.js';
import { constructorInputs } from './constructor-inputs.js';
import { Receivers } from './receivers.js';
import { typedConfigs } from './typed-configs.js';
import { isMemberAccess, memberName } from './member-access.js';

// Yield every possible first argument; return whether the sequence may be empty.
function* firstArguments(elements: readonly Node[], seen: ReadonlySet<Node> = new Set()): Generator<Node, boolean> {
  for (const element of elements) {
    if (Node.isOmittedExpression(element)) return false;
    if (!Node.isSpreadElement(element)) { yield element; return false; }
    const value = configValue(element.getExpression());
    if (Node.isArrayLiteralExpression(value) && !seen.has(value)) {
      if (!(yield* firstArguments(value.getElements(), new Set(seen).add(value)))) return false;
    } else {
      // An opaque spread is unresolved and may be empty, exposing a later slot.
      yield value;
    }
  }
  return true;
}

export function findConfigs(source: SourceFile, mapping: Mapping): readonly Node[] {
  const sources = new Set<Node>();
  const variables = new Set<Node>();
  const resolvedCalls = new Set<Node>();
  const calls = source.getDescendantsOfKind(SyntaxKind.CallExpression);
  const receivers = new Receivers(mapping);
  const collect = (node: Node, active: ReadonlySet<Node>, bindings: ReadonlyMap<Node, Node> = new Map()): void => {
    const value = unwrap(node);
    // Explicit undefined selects SDK defaults; a local namesake does not.
    if (Node.isIdentifier(value) && value.getText() === 'undefined' && declarations(value).length === 0) return;
    if (Node.isVoidExpression(value) && Node.isNumericLiteral(value.getExpression()) && value.getExpression().getText() === '0') return;
    if (active.has(value)) { sources.add(value); return; }
    const path = new Set([...active, value]);
    const bound = bindings.get(value);
    if (bound) { collect(bound, path, bindings); return; }
    if (Node.isVariableDeclaration(value) && value.getSourceFile() === source) {
      variables.add(value);
      const init = value.getInitializer();
      if (value.getVariableStatement()?.getDeclarationKind() === VariableDeclarationKind.Const && !bindingIsReassigned(value) && init) {
        collect(init, path, bindings);
      } else sources.add(value);
    } else if (Node.isParameterDeclaration(value) || Node.isBindingElement(value)) {
      sources.add(value);
      variables.add(value);
      for (const argument of constructorInputs(value)) collect(argument, path, bindings);
      const fallback = value.getInitializer();
      if (fallback) collect(fallback, path, bindings);
      const callable = value.getParent();
      if (Node.isFunctionDeclaration(callable) || Node.isArrowFunction(callable) || Node.isFunctionExpression(callable)) {
        const index = callable.getParameters().findIndex(parameter => parameter === value);
        for (const call of calls) {
          if (localConfigHelper(call) !== callable) continue;
          const argument = call.getArguments()[index];
          if (argument) collect(argument, path, bindings);
        }
      }
    } else if (Node.isObjectLiteralExpression(value)) {
      sources.add(value);
      const owner = value.getAncestors().find(Node.isVariableDeclaration);
      const init = owner?.getInitializer();
      if (owner && init && unwrap(init) === value && owner.getVariableStatement()?.getDeclarationKind() === VariableDeclarationKind.Const) variables.add(owner);
      for (const property of value.getProperties()) {
        if (Node.isSpreadAssignment(property)) {
          const spread = unwrap(property.getExpression());
          collect(spread, path, bindings);
        } else if (Node.isComputedPropertyName(property.getNameNode())) sources.add(property.getNameNode());
      }
    } else if (Node.isCallExpression(value) && isConfigAssign(value)) {
      resolvedCalls.add(value);
      for (const argument of value.getArguments()) collect(argument, path, bindings);
    } else if (Node.isCallExpression(value)) {
      const callable = localConfigHelper(value);
      const body = callable?.getBody();
      const statements = Node.isBlock(body) ? body.getStatements() : [];
      const first = statements[0];
      const returned = Node.isBlock(body) ? statements.length === 1 && Node.isReturnStatement(first) ? first.getExpression() : undefined : body;
      if (callable && returned) {
        const parameters = callable.getParameters();
        const arguments_ = value.getArguments();
        const boundArguments = new Map(bindings);
        for (const [index, parameter] of parameters.entries()) {
          const argument = arguments_[index] ?? parameter.getInitializer();
          if (argument) boundArguments.set(parameter, argument);
        }
        resolvedCalls.add(value);
        collect(returned, path, boundArguments);
      } else {
        sources.add(value);
        if (callable) {
          for (const returned of callable.getDescendantsOfKind(SyntaxKind.ReturnStatement)) {
            if (returned.getFirstAncestor(node => ts.isFunctionLike(node.compilerNode)) !== callable) continue;
            const expression = returned.getExpression();
            if (expression) collect(expression, path, bindings);
          }
        }
      }
    } else if (Node.isIdentifier(value)) {
      const definitions = configDeclarations(value);
      if (definitions.length && definitions.every(definition => definition.getSourceFile() === source &&
           (Node.isVariableDeclaration(definition) || Node.isParameterDeclaration(definition) || Node.isBindingElement(definition)))) {
        for (const definition of definitions) collect(definition, path, bindings);
      } else {
        sources.add(value);
        for (const definition of definitions) variables.add(definition);
      }
    } else sources.add(value);
  };
  for (const config of typedConfigs(source, mapping)) collect(config, new Set());
  for (const call of source.getDescendantsOfKind(SyntaxKind.NewExpression)) {
    // Own constructors define their own arguments; their SDK super call is checked below.
    const inherited = inheritsClientConstructor(call.getExpression(), mapping);
    if (!inherited && (isClientConstructor(call.getExpression(), mapping) || !receivers.client(call))) continue;
    const first = call.getArguments()[0];
    // A client result proves identity, not the unresolved constructor's argument contract.
    if (!inherited && first) sources.add(call);
    if (first) collect(first, new Set());
  }
  for (const call of calls) {
    const access = unwrap(call.getExpression());
    if (isMemberAccess(access)) {
      const receiver = unwrap(access.getExpression());
      const args = call.getArguments();
      const name = memberName(access);
      const definitions = receiver.getSymbol()?.getDeclarations() ?? [];
      const program = source.getProject().getProgram().compilerObject;
      const reflect = name === 'construct' && Node.isIdentifier(receiver) && receiver.getText() === 'Reflect' &&
        definitions.length > 0 && definitions.every(definition => program.isSourceFileDefaultLibrary(definition.getSourceFile().compilerNode));
      const target = reflect ? args[0] : name === 'bind' ? receiver : undefined;
      if (target && inheritsClientConstructor(target, mapping)) {
        const argument = args[1];
        const value = argument && configValue(argument);
        const candidates = reflect ? Node.isArrayLiteralExpression(value) ? firstArguments(value.getElements()) : value ? [value] : []
          : firstArguments(args.slice(1));
        resolvedCalls.add(call);
        for (const candidate of candidates) collect(candidate, new Set());
        continue;
      }
    }
    if (call.getExpression().getKind() !== SyntaxKind.SuperKeyword) continue;
    const owner = call.getFirstAncestorByKind(SyntaxKind.Constructor)?.getParent();
    const base = Node.isClassDeclaration(owner) || Node.isClassExpression(owner) ? owner.getExtends()?.getExpression() : undefined;
    if (!base || !inheritsClientConstructor(base, mapping)) continue;
    resolvedCalls.add(call);
    const first = call.getArguments()[0];
    if (first) collect(first, new Set());
  }
  let previousSize = -1;
  while (previousSize !== variables.size + sources.size) {
    previousSize = variables.size + sources.size;
    const owner = source;
    const aliases = owner.getDescendantsOfKind(SyntaxKind.VariableDeclaration);
    let added = true;
    while (added) {
      added = false;
      for (const alias of aliases) {
        const init = alias.getInitializer();
        if (variables.has(alias) || !init || alias.getVariableStatement()?.getDeclarationKind() !== VariableDeclarationKind.Const) continue;
        if (declarations(unwrap(init)).some(declaration => variables.has(declaration))) {
          variables.add(alias);
          added = true;
        }
      }
    }
    const tracked = (node: Node): boolean => sources.has(configValue(node)) || declarations(unwrap(node)).some(declaration => variables.has(declaration));
    for (const call of calls) {
      if (isConfigAssign(call)) {
        const target = call.getArguments()[0];
        if (target && tracked(target)) {
          for (const argument of call.getArguments().slice(1)) collect(argument, new Set());
        } else if (call.getArguments().some(argument => !tracked(argument) && containsConfig(argument, tracked))) {
          // Copying an aggregate publishes its contained config references.
          sources.add(call);
        }
        continue;
      }
      if (resolvedCalls.has(call) || isConfigReadOnlyCall(call)) continue;
      for (const [index, argument] of call.getArguments().entries()) {
        if (!containsConfig(argument, tracked)) continue;
        sources.add(call);
        const parameter = localConfigHelper(call)?.getParameters()[index];
        if (parameter) collect(parameter, new Set());
      }
    }
    for (const assignment of owner.getDescendantsOfKind(SyntaxKind.BinaryExpression)) {
      const operator = assignment.getOperatorToken().getKind();
      if (operator < SyntaxKind.FirstAssignment || operator > SyntaxKind.LastAssignment) continue;
      const access = assignment.getLeft();
      if (!(Node.isPropertyAccessExpression(access) || Node.isElementAccessExpression(access))) continue;
      if (tracked(access.getExpression())) sources.add(assignment);
      // Property storage can create aliases absent from the literal graph.
      // Refuse conservatively instead of silently trusting the old initializer.
      if (containsConfig(assignment.getRight(), tracked)) sources.add(access);
    }
  }
  return [...sources];
}
