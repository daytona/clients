/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { ParameterDeclaration, SourceFile } from 'ts-morph';
import { declarations } from './bindings.js';
import { propertyKey } from './binding-key.js';
import type { ConsumerRequirement } from './double-consumer-flow.js';
import { initializer, statement, unwrap } from './edits.js';
import { rewrittenParameter } from './local-double-consumers.js';
import type { DoubleConsumers } from './local-double-consumers.js';
import { mockFactory } from './mock-factories.js';
import type { Receivers } from './receivers.js';
import { bindingIsStable } from './stable-bindings.js';

export const unresolvedDoubleMessage = 'Mock replacement reaches a Daytona client consumer but its v0 shape is unresolved; give the replacement a v0 attribute before using SDK 2.x.';

type ArgumentRequirement = ConsumerRequirement & { readonly receivers: Receivers };

function sdkArgument(node: Node, requirement: ArgumentRequirement, seen = new Set<Node>()): boolean {
  if (requirement.member === undefined) return false;
  const value = unwrap(node);
  if (seen.has(value) || seen.size >= 64 || mockFactory(value)) return false;
  const next = new Set(seen).add(value);
  const bindings = declarations(value);
  const initialized = bindings.filter(binding => initializer(binding) !== undefined);
  // A visible initializer outranks a cast or an annotation on its binding.
  if (initialized.length) return initialized.every(binding => {
    const init = initializer(binding);
    return init !== undefined && bindingIsStable(binding) && sdkArgument(init, requirement, next);
  });
  if (requirement.path.length) {
    const [key, ...path] = requirement.path;
    if (key === undefined) return false;
    const selected = { ...requirement, path };
    if (Node.isObjectLiteralExpression(value)) {
      const properties = value.getProperties();
      // Spreads and duplicate/dynamic keys cannot certify the selected value.
      if (properties.some(property => propertyKey(property) === undefined)) return false;
      const matches = properties.filter(property => propertyKey(property) === key);
      const property = matches.length === 1 ? matches[0] : undefined;
      const payload = property && (initializer(property) ?? (Node.isShorthandPropertyAssignment(property) ? property.getNameNode() : undefined));
      return payload !== undefined && sdkArgument(payload, selected, next);
    }
    if (Node.isArrayLiteralExpression(value)) {
      const index = Number(key);
      if (!Number.isInteger(index) || index < 0 || String(index) !== key) return false;
      const payload = value.getElements()[index];
      return !value.getElements().some(Node.isSpreadElement) && payload !== undefined && sdkArgument(payload, selected, next);
    }
    const properties = value.getType().getProperty(key)?.getDeclarations() ?? [];
    return properties.length > 0 && properties.every(property => sdkArgument(property, selected, next));
  }
  return requirement.receivers.client(value, requirement.member);
}

/** Review is broader than adaptation: one moving consumer access is enough. */
export function unresolvedClientDoubles(source: SourceFile, context: DoubleConsumers, adapted: ReadonlySet<Node>): void {
  const moving = new Map<ParameterDeclaration, readonly ConsumerRequirement[]>();
  for (const call of [...source.getDescendantsOfKind(SyntaxKind.CallExpression), ...source.getDescendantsOfKind(SyntaxKind.NewExpression)]) {
    for (const [position, argument] of call.getArguments().entries()) {
      const value = unwrap(argument, true);
      if (adapted.has(value) || declarations(value).some(binding => adapted.has(binding))) continue;
      const type = call.getExpression().getType();
      const signatures = Node.isNewExpression(call) ? type.getConstructSignatures() : type.getCallSignatures();
      const requirements = signatures.flatMap(signature => signature.getParameters().flatMap((symbol, index) =>
        symbol.getDeclarations().flatMap(parameter => {
          if (!Node.isParameterDeclaration(parameter) || (parameter.isRestParameter() ? position < index : position !== index)) return [];
          const changes = moving.get(parameter) ?? rewrittenParameter(parameter, context);
          moving.set(parameter, changes);
          if (!parameter.isRestParameter()) return changes;
          return changes.flatMap(change => change.path[0] === undefined || change.path[0] === String(position - index)
            ? [{ ...change, path: change.path.slice(1) }] : []);
        })));
      if (requirements.some(requirement => !sdkArgument(value, { ...requirement, receivers: context.receivers }))) {
        context.reviews.add(argument);
      }
    }
  }
  // Keep the manual item after consumer accesses have moved and types changed.
  if (!source.getFullText().includes(`// FIXME(daytona-upgrade): ${unresolvedDoubleMessage}`)) return;
  for (const node of source.getDescendants().filter(Node.isStatement)) {
    if (node.getLeadingCommentRanges().some(comment => comment.getText() === `// FIXME(daytona-upgrade): ${unresolvedDoubleMessage}`)) {
      if (![...context.reviews].some(review => statement(review) === node)) context.reviews.add(node.getFirstChildOrThrow());
    }
  }
}
