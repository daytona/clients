/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { declarations } from './bindings.js';
import { consumerRequirements } from './double-consumer-flow.js';
import { initializer, unwrap } from './edits.js';
import type { DoubleConsumers } from './local-double-consumers.js';
import { isMemberAccess, memberName } from './member-access.js';
import { isModuleLiteral } from './module-loader.js';
import { replacementHasV0 } from './mock-replacement-shape.js';
import { resolveProjectModule } from './project-modules.js';
import { testHelper } from './test-bindings.js';

type RewrittenFactories = ReadonlyMap<Node, ReadonlySet<string | undefined>>;
const rewritten = new WeakMap<ReadonlySet<SourceFile>, RewrittenFactories>();
const moduleMocks: readonly string[] = ['mock', 'doMock', 'unstable_mockModule'];
const returnSetters: readonly string[] = ['mockReturnValue', 'mockReturnValueOnce', 'mockResolvedValue', 'mockResolvedValueOnce'];
const implementationSetters: readonly string[] = ['mockImplementation', 'mockImplementationOnce'];
const rejectionSetters: readonly string[] = ['mockRejectedValue', 'mockRejectedValueOnce'];
const setters = [...returnSetters, ...implementationSetters, ...rejectionSetters];

function factoryDeclarations(node: Node, seen: ReadonlySet<Node> = new Set()): readonly (Node | undefined)[] {
  const value = unwrap(node, true);
  if (seen.has(value)) return [];
  if (seen.size >= 64) return [undefined];
  const next = new Set(seen).add(value);
  const targets = declarations(value);
  const init = initializer(value);
  return [value, ...targets.flatMap(target => factoryDeclarations(target, next)),
    ...(init ? factoryDeclarations(init, next) : [])];
}

/** Index actual moving result uses, not merely factories with SDK return annotations. */
function rewrittenFactories(context: DoubleConsumers): RewrittenFactories {
  const cached = rewritten.get(context.sources);
  if (cached) return cached;
  const factories = new Map<Node, ReadonlySet<string | undefined>>();
  for (const source of context.sources) {
    for (const call of source.getDescendantsOfKind(SyntaxKind.CallExpression)) {
      const targets = factoryDeclarations(call.getExpression()).filter((target): target is Node =>
        target !== undefined && context.sources.has(target.getSourceFile()) && !target.getSourceFile().isDeclarationFile() &&
        (Node.isFunctionDeclaration(target) || Node.isArrowFunction(target) || Node.isFunctionExpression(target) ||
          Node.isMethodDeclaration(target) || Node.isVariableDeclaration(target)));
      if (!targets.length) continue;
      const members = consumerRequirements(call, context).map(requirement => requirement.member);
      if (!members.length) continue;
      for (const target of targets) factories.set(target, new Set([...(factories.get(target) ?? []), ...members]));
    }
  }
  rewritten.set(context.sources, factories);
  return factories;
}

/** Review replacements without changing their identity or guessing at a runtime mock's shape. */
export function factoryMockTargets(source: SourceFile, context: DoubleConsumers): void {
  const calls = source.getDescendantsOfKind(SyntaxKind.CallExpression);
  if (!calls.some(call => moduleMocks.includes(testHelper(call.getExpression()) ?? '') ||
      isMemberAccess(call.getExpression()) && setters.includes(memberName(call.getExpression()) ?? ''))) return;
  const factories = rewrittenFactories(context);
  const memberPath = (member: string): readonly (string | null)[] => ['v0', member,
    ...(Object.values(context.receivers.mapping.receivers).some(entry => entry.parent === 'client' && entry.attr?.ts === member) ? [] : [null])];
  const requirements = (node: Node): readonly (string | undefined)[] => [...new Set(factoryDeclarations(node).flatMap(target =>
    target === undefined ? [undefined] : [...(factories.get(target) ?? [])]))];
  const mockedFactory = (node: Node): readonly (string | undefined)[] => {
    const value = unwrap(node, true);
    if (!Node.isCallExpression(value)) return requirements(value);
    const helper = testHelper(value.getExpression());
    const [target, key] = value.getArguments();
    if (helper === 'mocked') return target !== undefined ? mockedFactory(target) : [];
    if (helper === 'spyOn' && target && isModuleLiteral(key)) {
      return (target.getType().getProperty(key.getLiteralValue())?.getDeclarations() ?? []).flatMap(requirements);
    }
    const callee = unwrap(value.getExpression(), true);
    return isMemberAccess(callee) && setters.includes(memberName(callee) ?? '') ?
      mockedFactory(callee.getExpression()) : [];
  };
  for (const call of calls) {
    const callee = unwrap(call.getExpression(), true);
    if (moduleMocks.includes(testHelper(callee) ?? '')) {
      const [specifier, replacement] = call.getArguments();
      if (!isModuleLiteral(specifier)) continue;
      const module = resolveProjectModule(source, specifier.getLiteralValue());
      if (!module || !context.sources.has(module)) continue;
      const exports = [...module.getExportedDeclarations()];
      if (exports.some(([name, targets]) => targets.flatMap(requirements).some(member =>
        member === undefined || !replacement || !replacementHasV0(replacement, [null, name, null, ...memberPath(member)])))) context.reviews.add(call);
      continue;
    }
    if (!isMemberAccess(callee)) continue;
    const method = memberName(callee) ?? '';
    if (!returnSetters.includes(method) && !implementationSetters.includes(method)) continue;
    const replacement = call.getArguments()[0];
    if (mockedFactory(callee.getExpression()).some(member => member === undefined || !replacement ||
        !replacementHasV0(replacement, [...(implementationSetters.includes(method) ? [null] : []), ...memberPath(member)]))) context.reviews.add(call);
  }
}
