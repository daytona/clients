/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Edit, ItemKind } from './model.js';
import type { Receivers } from './receivers.js';
import type { OutsideProviders } from './outside-providers.js';
import { importedName, isClientConstructor } from './bindings.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { testHelper } from './test-bindings.js';

export function spyTargets(source: SourceFile, receivers: Receivers, context: {
  readonly manual: (node: Node, message: string, kind?: ItemKind) => void;
  readonly outside: OutsideProviders;
}): readonly Edit[] {
  const edits: Edit[] = [];
  for (const call of source.getDescendantsOfKind(SyntaxKind.CallExpression)) {
    const callee = unwrap(call.getExpression());
    if (testHelper(callee) !== 'spyOn') continue;
    const [target, key] = call.getArguments();
    if (!target || !key || Node.isSpreadElement(target) || Node.isSpreadElement(key)) continue;
    const receiver = unwrap(target, true);
    if (['v0', 'v2'].includes(memberName(receiver) ?? '')) continue;
    const literal = unwrap(key);
    const member = Node.isStringLiteral(literal) || Node.isNoSubstitutionTemplateLiteral(literal) ? literal.getLiteralValue() : undefined;
    if (member !== undefined && !receivers.mapping.movedClientMembers.ts.includes(member)) continue;
    const constructor = isMemberAccess(receiver) && memberName(receiver) === 'prototype' ? receiver.getExpression() : undefined;
    const prototype = constructor !== undefined && (isClientConstructor(constructor, receivers.mapping) ||
      ['Daytona', 'AsyncDaytona'].includes(importedName(constructor, receivers.mapping) ?? ''));
    if (prototype) {
      context.manual(call, 'Mock target references a moved root-client member; patch the v0 client instead.', 'mock-target');
    } else if (receivers.client(receiver, member)) {
      for (const message of context.outside.messages(receiver)) context.manual(call, message);
      if (member === undefined) {
        context.manual(call, 'Review unresolved mock target against the v0 namespace.', 'mock-target');
      } else {
        // Insertion retains nested edits and comments; parentheses give any expression a property-access receiver.
        const grouped = Node.isIdentifier(target) || isMemberAccess(target) || Node.isParenthesizedExpression(target) ||
          Node.isCallExpression(target) || Node.isNewExpression(target) && target.getFirstChildByKind(SyntaxKind.OpenParenToken) !== undefined;
        if (!grouped) edits.push({ start: target.getStart(), end: target.getStart(), text: '(' });
        edits.push({ start: target.getEnd(), end: target.getEnd(), text: grouped ? '.v0' : ').v0' });
      }
    } else if (receivers.plausible(receiver, member)) {
      context.manual(call, 'Review unresolved mock target against the v0 namespace.', 'mock-target');
    }
  }
  return edits;
}
