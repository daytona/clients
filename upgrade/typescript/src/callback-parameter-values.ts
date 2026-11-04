/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { CallExpression, NewExpression, ParameterDeclaration } from 'ts-morph';
import { declarations } from './bindings.js';
import { bindingKey, propertyKey } from './binding-key.js';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { CallbackAnalysis } from './callback-analysis.js';

export type Invocation = CallExpression | NewExpression;
type Context = { readonly calls: readonly Invocation[]; readonly seen: ReadonlySet<Node>; readonly work: CallbackAnalysis };
export type SuppliedValue = { readonly node: Node; readonly calls: readonly Invocation[] };

/** Select only an identifiable own callback, retaining its supplying invocation. */
function callbackProperty(node: Node, key: string, context: Context): readonly SuppliedValue[] {
  const value = unwrap(node);
  if (!context.work.enter(value, context.seen)) return [];
  const next = { ...context, seen: new Set(context.seen).add(value) };
  if (Node.isParameterDeclaration(value)) {
    const writes = value.getParent().getDescendantsOfKind(SyntaxKind.BinaryExpression).filter(write => {
      const target = unwrap(write.getLeft());
      const operator = write.getOperatorToken().getKind();
      return operator >= SyntaxKind.FirstAssignment && operator <= SyntaxKind.LastAssignment &&
        isMemberAccess(target) && (memberName(target) === key || memberName(target) === undefined) &&
        declarations(target.getExpression()).includes(value);
    });
    return (suppliedValues(value, context) ?? []).flatMap(argument => {
      const invocation = { ...next, calls: argument.calls };
      // A wrapper write severs call-site property identity. Its possible
      // replacements still receive the invocation's arguments as review evidence.
      return writes.length ? writes.flatMap(write => callbacks(write.getRight(), invocation)) :
        callbackProperty(argument.node, key, invocation);
    });
  }
  if (Node.isObjectLiteralExpression(value)) {
    const properties = value.getProperties();
    // Spreads, unknown keys and duplicates cannot identify a single callback.
    if (properties.some(property => propertyKey(property) === undefined)) return [];
    const matching = properties.filter(property => propertyKey(property) === key);
    return matching.length === 1 ? matching.flatMap(property => callbacks(property, next)) : [];
  }
  const init = initializer(value);
  if (init) return callbackProperty(init, key, next);
  return declarations(value).flatMap(provider => callbackProperty(provider, key, next));
}

/** Resolve callable identities without treating the callback object as its result. */
function callbacks(node: Node, context: Context): readonly SuppliedValue[] {
  const value = unwrap(node);
  if (!context.work.enter(value, context.seen)) return [];
  const next = { ...context, seen: new Set(context.seen).add(value) };
  if (Node.isFunctionDeclaration(value) || Node.isFunctionExpression(value) || Node.isArrowFunction(value) ||
    Node.isMethodDeclaration(value)) return [{ node: value, calls: context.calls }];
  if (Node.isClassDeclaration(value) || Node.isClassExpression(value)) {
    return value.getConstructors().map(node => ({ node, calls: context.calls }));
  }
  if (Node.isParameterDeclaration(value)) {
    return (suppliedValues(value, context) ?? []).flatMap(argument => callbacks(argument.node, { ...next, calls: argument.calls }));
  }
  if (isMemberAccess(value)) {
    const key = memberName(value);
    const selected = key === undefined ? [] : callbackProperty(value.getExpression(), key, next);
    if (selected.length) return selected;
  }
  if (Node.isBindingElement(value) && Node.isObjectBindingPattern(value.getParent()) && !value.getDotDotDotToken()) {
    const owner = value.getParent().getParent();
    const key = bindingKey(value);
    const source = Node.isParameterDeclaration(owner) ? owner : initializer(owner);
    return source && key !== undefined ? callbackProperty(source, key, next) : [];
  }
  if (Node.isConditionalExpression(value)) {
    return [value.getWhenTrue(), value.getWhenFalse()].flatMap(branch => callbacks(branch, next));
  }
  const init = initializer(value);
  if (init) return callbacks(init, next);
  return declarations(value).flatMap(provider => callbacks(provider, next));
}

function suppliedValues(parameter: ParameterDeclaration, context: Context): readonly SuppliedValue[] | undefined {
  if (!context.work.enter(parameter, context.seen)) return [];
  return context.work.memoized(parameter, context, () => resolveSuppliedValues(parameter, context));
}

function resolveSuppliedValues(parameter: ParameterDeclaration, context: Context): readonly SuppliedValue[] {
  const owner = parameter.getParent();
  if (!Node.isFunctionDeclaration(owner) && !Node.isFunctionExpression(owner) && !Node.isArrowFunction(owner) &&
    !Node.isMethodDeclaration(owner) && !Node.isConstructorDeclaration(owner)) return [];
  const position = owner.getParameters().indexOf(parameter);
  const binding = Node.isFunctionExpression(owner) || Node.isArrowFunction(owner) || Node.isConstructorDeclaration(owner) ? owner.getParent() : owner;
  const next = { ...context, seen: new Set(context.seen).add(parameter) };
  const supplied = (call: Invocation): readonly SuppliedValue[] | undefined => {
    if (!context.work.step()) return undefined;
    const targets = declarations(call.getExpression()).includes(binding) ? [{ node: owner, calls: context.calls }] :
      callbacks(call.getExpression(), next);
    const matching = targets.filter(target => target.node === owner);
    if (!matching.length) return undefined;
    const argument = call.getArguments()[position];
    return argument ? matching.map(target => ({ node: argument, calls: [...target.calls, call] })) : [];
  };
  // A factory return belongs to this invocation, not an unrelated caller.
  for (const call of [...context.calls].reverse()) {
    if (!context.work.step()) return [];
    const values = supplied(call);
    if (values !== undefined) return values;
  }
  const values: SuppliedValue[] = [];
  for (const call of context.work.candidates(owner)) {
    values.push(...supplied(call) ?? []);
  }
  return values;
}

/** Incomplete searches are review-only, never defaultable or silently unknown. */
export function parameterValues(parameter: ParameterDeclaration, calls: readonly Invocation[]): readonly SuppliedValue[] | 'incomplete' {
  const work = new CallbackAnalysis(parameter.getProject());
  return work.finish(suppliedValues(parameter, { calls, seen: new Set(), work }));
}
