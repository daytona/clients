/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { Invocation } from './callback-parameter-values.js';
import { declarations } from './bindings.js';
import { bindingKey, propertyKey } from './binding-key.js';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { bindingIsStable } from './stable-bindings.js';
import { parameterValues } from './callback-parameter-values.js';

export { parameterValues } from './callback-parameter-values.js';

type FlowContext = { readonly calls: readonly Invocation[]; readonly seen: ReadonlySet<Node>; readonly incomplete: Set<Node> };
export type PayloadSelection = { readonly definite: readonly Node[]; readonly evidence: readonly Node[] };

/** Fold properties in write order, retaining possible payloads until a definite replacement. */
function objectPayloads(value: Node, context: FlowContext & { readonly key: string }, previous: PayloadSelection): PayloadSelection {
  if (!Node.isObjectLiteralExpression(value) || context.seen.has(value) || context.seen.size >= 64) {
    return { definite: [], evidence: previous.evidence };
  }
  const next = { ...context, seen: new Set(context.seen).add(value) };
  let selected = previous;
  for (const property of value.getProperties()) {
    if (Node.isSpreadAssignment(property)) {
      const alternatives = flowValues(property.getExpression(), next).map(object => objectPayloads(object, next, selected));
      selected = {
        definite: alternatives.every(alternative => alternative.definite.length > 0) ? alternatives.flatMap(alternative => alternative.definite) : [],
        evidence: alternatives.flatMap(alternative => alternative.evidence),
      };
      continue;
    }
    const name = propertyKey(property);
    if (name !== undefined && name !== context.key) continue;
    // Defining a setter preserves an existing getter; it does not replace its read value.
    if (Node.isSetAccessorDeclaration(property) && name === context.key &&
      selected.definite.length && selected.definite.every(Node.isGetAccessorDeclaration)) continue;
    const payload = initializer(property) ?? (Node.isShorthandPropertyAssignment(property) ? property.getNameNode() : property);
    selected = { definite: name === context.key && bindingIsStable(property) ? [payload] : [],
      evidence: [...selected.evidence, payload] };
  }
  return selected;
}

/** Resolve values, keeping opaque alternatives instead of dropping them. */
function flowValues(node: Node, context: FlowContext): readonly Node[] {
  const value = unwrap(node);
  if (context.seen.has(value) || context.seen.size >= 64) return [value];
  if (!bindingIsStable(value)) return [value];
  const next = { ...context, seen: new Set(context.seen).add(value) };
  if (Node.isIdentifier(value)) {
    const providers = declarations(value);
    return providers.length ? providers.flatMap(provider => flowValues(provider, next)) : [value];
  }
  if (Node.isParameterDeclaration(value)) {
    const supplied = parameterValues(value, context.calls);
    if (supplied === 'incomplete') { context.incomplete.add(value); return [value]; }
    return supplied.length ? supplied.flatMap(argument => flowValues(argument.node, { ...next, calls: argument.calls })) : [value];
  }
  if (Node.isConditionalExpression(value)) {
    return [value.getWhenTrue(), value.getWhenFalse()].flatMap(branch => flowValues(branch, next));
  }
  const init = initializer(value);
  if (init) return flowValues(init, next);
  if (!Node.isCallExpression(value)) return [value];
  const invocation = { ...next, calls: [...context.calls, value] };
  return flowValues(value.getExpression(), next).flatMap(factory => {
    if (!Node.isFunctionDeclaration(factory) && !Node.isFunctionExpression(factory) && !Node.isArrowFunction(factory) &&
      !Node.isMethodDeclaration(factory)) return [value];
    const body = factory.getBody();
    if (!body) return [value];
    if (!Node.isBlock(body)) return flowValues(body, invocation);
    const returned: Node[] = [];
    body.forEachDescendant((child, traversal) => {
      if (Node.isFunctionLikeDeclaration(child)) { traversal.skip(); return; }
      if (Node.isReturnStatement(child)) returned.push(child.getExpression() ?? value);
    });
    return returned.length ? returned.flatMap(result => flowValues(result, invocation)) : [value];
  });
}

/** Project a destructured value from local factory results, not the whole object. */
export function bindingValues(binding: Node, calls: readonly Invocation[]): readonly PayloadSelection[] {
  if (!Node.isBindingElement(binding) || !Node.isObjectBindingPattern(binding.getParent())) return [];
  const variable = binding.getParent().getParent();
  if (!Node.isVariableDeclaration(variable) || variable.getNameNode() !== binding.getParent()) return [];
  const init = initializer(variable);
  const key = bindingKey(binding);
  if (!init || key === undefined || binding.getDotDotDotToken()) return [];
  const context = { calls, seen: new Set<Node>(), incomplete: new Set<Node>(), key };
  const selected = flowValues(init, context).map(value => objectPayloads(value, context, { definite: [], evidence: [] }));
  return [...selected, ...context.incomplete.size ? [{ definite: [], evidence: [...context.incomplete] }] : []];
}

/** Select the member's payload, not the object carrying a call argument. */
export function memberValues(node: Node, calls: readonly Invocation[]): readonly PayloadSelection[] {
  if (!isMemberAccess(node)) return [];
  const key = memberName(node);
  if (key === undefined) return [];
  const context = { calls, seen: new Set<Node>(), incomplete: new Set<Node>(), key };
  const values = flowValues(node.getExpression(), context);
  const selected = values.some(Node.isObjectLiteralExpression) ?
    values.map(value => objectPayloads(value, context, { definite: [], evidence: [] })) : [];
  return [...selected, ...context.incomplete.size ? [{ definite: [], evidence: [...context.incomplete] }] : []];
}

/** Matching insertions are linked evidence, never proof of a retrieved value. */
export function containerInsertions(node: Node): readonly Node[] {
  if (!Node.isCallExpression(node)) return [];
  const access = unwrap(node.getExpression());
  if (!isMemberAccess(access) || memberName(access) !== 'get') return [];
  const key = node.getArguments()[0];
  if (!key) return [];
  const container = declarations(access.getExpression());
  if (!container.length) return [];
  const values: Node[] = [];
  for (const source of node.getProject().getSourceFiles()) {
    if (source.isDeclarationFile() || source.isInNodeModules()) continue;
    for (const call of source.getDescendantsOfKind(SyntaxKind.CallExpression)) {
      const write = unwrap(call.getExpression());
      if (!isMemberAccess(write) || memberName(write) !== 'set' ||
        !declarations(write.getExpression()).some(declaration => container.includes(declaration))) continue;
      const [insertedKey, payload] = call.getArguments();
      if (!insertedKey || !payload) continue;
      const sameKey = (Node.isStringLiteral(key) || Node.isNoSubstitutionTemplateLiteral(key)) &&
        (Node.isStringLiteral(insertedKey) || Node.isNoSubstitutionTemplateLiteral(insertedKey)) ? key.getLiteralValue() === insertedKey.getLiteralValue() :
        Node.isNumericLiteral(key) && Node.isNumericLiteral(insertedKey) ? key.getLiteralValue() === insertedKey.getLiteralValue() :
        Node.isIdentifier(key) && Node.isIdentifier(insertedKey) &&
          declarations(key).some(declaration => declarations(insertedKey).includes(declaration));
      if (sameKey) values.push(payload);
    }
  }
  return values;
}
