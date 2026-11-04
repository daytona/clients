/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import { bindingKey } from './binding-key.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import type { Receivers } from './receivers.js';
import type { DoubleConsumers } from './local-double-consumers.js';

// An unknown member records an analysis cutoff, never a satisfied requirement.
export type ConsumerRequirement = { readonly path: readonly (string | undefined)[]; readonly member: string | undefined };

/** Match the migration pass: extracted methods are reviewed, not moved. */
export function movingMember(use: Node, receivers: Receivers): string | undefined {
  const access = use.getAncestors().find(parent => unwrap(parent) !== use);
  if (!access || !isMemberAccess(access) || unwrap(access.getExpression()) !== use) return undefined;
  const member = memberName(access);
  if (member === undefined || !receivers.mapping.movedClientMembers.ts.includes(member) || !receivers.client(access.getExpression(), member)) return undefined;
  const service = Object.values(receivers.mapping.receivers).some(entry => entry.parent === 'client' && entry.attr?.ts === member);
  const call = access.getAncestors().find(parent => unwrap(parent) !== access);
  return service || Node.isCallExpression(call) && unwrap(call.getExpression()) === access ? member : undefined;
}

/** Review-only value flow; never a certificate that wrapping preserves identity. */
export function consumerRequirements(name: Node, context: DoubleConsumers): readonly ConsumerRequirement[] {
  const requirements: ConsumerRequirement[] = [];
  type Flow = { readonly path: ConsumerRequirement['path']; readonly seen: ReadonlySet<Node>; readonly slot?: string };
  const bind = (binding: Node, flow: Flow): void => {
    if (Node.isIdentifier(binding)) {
      for (const use of binding.findReferencesAsNodes()) {
        const parent = use.getParent();
        // Parameter properties have both a parameter symbol and a member symbol.
        visit(Node.isPropertyAccessExpression(parent) && parent.getNameNode() === use ? parent : use, flow);
      }
    } else if (Node.isObjectBindingPattern(binding) || Node.isArrayBindingPattern(binding)) {
      for (const [index, element] of binding.getElements().entries()) {
        if (!Node.isBindingElement(element)) continue;
        const key = element.getDotDotDotToken() ? undefined : Node.isArrayBindingPattern(binding) ? String(index) : bindingKey(element);
        if (flow.slot === undefined) bind(element.getNameNode(), { ...flow, path: [...flow.path, key] });
        else if (key === flow.slot) bind(element.getNameNode(), { path: flow.path, seen: flow.seen });
        else if (key === undefined) requirements.push({ path: flow.path, member: undefined });
      }
    }
  };
  const visit = (node: Node, flow: Flow): void => {
    const value = unwrap(node);
    if (!context.sources.has(value.getSourceFile()) || flow.seen.has(value)) return;
    // An exhausted budget cannot certify that a replacement has no moving consumer.
    if (flow.seen.size >= 64) { requirements.push({ path: flow.path, member: undefined }); return; }
    const next = { ...flow, seen: new Set(flow.seen).add(value) };
    const member = movingMember(value, context.receivers);
    if (member !== undefined) requirements.push({ path: flow.path, member });
    const parent = value.getAncestors().find(ancestor => unwrap(ancestor) !== value);
    if ((Node.isPropertyAccessExpression(parent) || Node.isElementAccessExpression(parent)) && unwrap(parent.getExpression()) === value) {
      const index = Node.isElementAccessExpression(parent) ? parent.getArgumentExpression() : undefined;
      const key = Node.isNumericLiteral(index) ? String(index.getLiteralValue()) : memberName(parent);
      if (flow.slot === undefined) visit(parent, { ...next, path: [...flow.path, key] });
      else if (Node.isElementAccessExpression(parent) && (key === undefined || key === flow.slot)) {
        visit(parent, { path: flow.path, seen: next.seen });
      }
    } else if ((Node.isVariableDeclaration(parent) || Node.isPropertyDeclaration(parent)) &&
        parent.getInitializer() !== undefined && unwrap(parent.getInitializerOrThrow()) === value) {
      bind(parent.getNameNode(), next);
    } else if (Node.isConditionalExpression(parent) &&
        (unwrap(parent.getWhenTrue()) === value || unwrap(parent.getWhenFalse()) === value)) {
      visit(parent, next);
    } else if (Node.isCallExpression(parent)) {
      const position = parent.getArguments().findIndex(argument => unwrap(argument) === value);
      if (position < 0) return;
      for (const signature of parent.getExpression().getType().getCallSignatures()) {
        const parameters = signature.getParameters();
        const rest = parameters.findIndex(parameter => parameter.getDeclarations().some(declaration =>
          Node.isParameterDeclaration(declaration) && declaration.isRestParameter()));
        const parameterIndex = rest >= 0 && position >= rest ? rest : position;
        for (const parameter of parameters[parameterIndex]?.getDeclarations() ?? []) {
          if (!Node.isParameterDeclaration(parameter) ||
              !context.sources.has(parameter.getSourceFile()) || parameter.getSourceFile().isDeclarationFile()) continue;
          bind(parameter.getNameNode(), parameter.isRestParameter() ? { ...next, slot: String(position - parameterIndex) } : next);
        }
      }
    } else if (Node.isBinaryExpression(parent)) {
      const operator = parent.getOperatorToken().getKind();
      if (operator === SyntaxKind.EqualsToken && unwrap(parent.getRight()) === value) {
        const target = unwrap(parent.getLeft());
        if (Node.isIdentifier(target)) bind(target, next);
        else if (Node.isPropertyAccessExpression(target)) bind(target.getNameNode(), next);
      } else if (operator === SyntaxKind.AmpersandAmpersandToken) {
        // A constructed object is truthy: only the right operand can supply a client.
        if (unwrap(parent.getRight()) === value || !Node.isNewExpression(unwrap(parent.getRight()))) visit(parent, next);
      } else if ([SyntaxKind.QuestionQuestionToken, SyntaxKind.BarBarToken].includes(operator)) visit(parent, next);
    }
  };
  if (Node.isCallExpression(name)) visit(name, { path: [], seen: new Set() });
  else bind(name, { path: [], seen: new Set() });
  return requirements;
}
