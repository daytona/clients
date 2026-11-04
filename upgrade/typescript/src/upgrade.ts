/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Edit, Item, ItemKind, Mapping } from './model.js';
import { imports } from './imports.js';
import { configs } from './config.js';
import { Receivers } from './receivers.js';
import { annotatedKind, isClientConstructor } from './bindings.js';
import { applyEdits, commentEdit, displayText, item, statement, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { typeMembers } from './type-members.js';
import { OutsideProviders } from './outside-providers.js';
import { spyTargets } from './spy-targets.js';
import { bindingKey } from './binding-key.js';
import { reportMemberWrites } from './member-writes.js';
import { reportConstructorErrors } from './constructor-errors.js';
import { localClientDoubles } from './local-client-doubles.js';
import { unresolvedDoubleMessage } from './unresolved-client-doubles.js';

const staticHelperMessage = 'Review Daytona.createAxiosInstance manually: SDK 2.x has no public Daytona static helper; use a standalone HTTP client.';

export function upgrade(source: SourceFile, mapping: Mapping, context: {
  readonly root: string; readonly dropApiUrl: boolean; readonly configObjects?: readonly Node[];
  readonly workspaceNames?: readonly string[];
  readonly reportedOutsideProviders?: Set<string>;
  readonly sources?: ReadonlySet<SourceFile>;
}): { readonly after: string; readonly items: readonly Item[] } {
  const importResult = imports(source, mapping, context.root);
  const configResult = configs(source, mapping, context);
  const edits: Edit[] = [...importResult.edits, ...configResult.edits];
  const items: Item[] = [...importResult.items, ...configResult.items];
  const receivers = new Receivers(mapping, context.workspaceNames);
  const doubles = localClientDoubles(source, receivers, context.sources ?? new Set([source]));
  edits.push(...doubles.edits);
  const inheritsStaticHelper = (receiver: Node): boolean => isClientConstructor(receiver, mapping,
    { seen: new Set(), inheritedOnly: false, staticMember: 'createAxiosInstance' });
  const outside = new OutsideProviders(source, context.root, context.reportedOutsideProviders);
  const comments = new Map<ReturnType<typeof statement>, string[]>();
  const manual = (node: Node, message: string, kind: ItemKind = 'fixme'): void => {
    items.push(item(node, { kind, message }, context.root));
    const owner = statement(node);
    const line = `FIXME(daytona-upgrade): ${message}`;
    const previous = source.getFullText().slice(owner.getFullStart(), owner.getStart());
    if (!previous.includes(commentEdit(owner, [line]).text.trim())) comments.set(owner, [...new Set([...(comments.get(owner) ?? []), line])]);
  };
  for (const review of configResult.reviews) manual(review.node, review.message, 'config-field');
  for (const node of doubles.reviews) manual(node, unresolvedDoubleMessage, 'mock-target');
  edits.push(...typeMembers(source, receivers, manual));
  edits.push(...spyTargets(source, receivers, { manual, outside }));
  reportMemberWrites(source, receivers, manual);
  const v0Receivers: Node[] = [];
  for (const access of source.getDescendants().filter(node => Node.isPropertyAccessExpression(node) || Node.isElementAccessExpression(node))) {
    if (doubles.accesses.has(access)) continue;
    const member = memberName(access);
    const receiver = access.getExpression();
    if (member === 'v0') v0Receivers.push(receiver);
    if (member === 'createAxiosInstance' && inheritsStaticHelper(receiver)) {
      manual(access, staticHelperMessage);
      continue;
    }
    if (member === 'v0' && receivers.client(receiver)) {
      for (const message of outside.messages(receiver)) manual(access, message);
    }
    if (['v0', 'v2'].includes(memberName(receiver) ?? '')) continue;
    if (member === undefined) {
      if (receivers.plausible(receiver)) manual(access,
        `review dynamic member lookup on \`${displayText(receiver.getText())}\`; SDK 2.x moved client methods to .v0`);
      continue;
    }
    if (!mapping.movedClientMembers.ts.includes(member)) continue;
    const proven = receivers.client(receiver, member);
    if (proven) for (const message of outside.messages(receiver)) manual(access, message);
    const service = Object.values(mapping.receivers).some(entry => entry.parent === 'client' && entry.attr?.ts === member);
    const call = access.getAncestors().find(parent => unwrap(parent) !== access);
    const directlyCalled = Node.isCallExpression(call) && unwrap(call.getExpression()) === access;
    if (proven && !service && !directlyCalled) {
      manual(access, `Review extracted client method \`${displayText(access.getText())}\` manually: moving it to .v0 changes its this receiver.`);
    } else if (proven) {
      const optional = access.getFirstChildByKind(SyntaxKind.QuestionDotToken);
      const start = optional ? Node.isPropertyAccessExpression(access) ? access.getNameNode().getStart() : optional.getEnd() : receiver.getEnd();
      edits.push({ start, end: start, text: optional ? Node.isPropertyAccessExpression(access) ? 'v0.' : 'v0' : '.v0' });
      v0Receivers.push(receiver);
    } else if (receivers.plausible(receiver, member)) {
      const text = displayText(receiver.getText());
      const target = Node.isElementAccessExpression(access) ? `${text}.v0[${displayText(access.getArgumentExpressionOrThrow().getText())}]` : `${text}.v0.${member}`;
      const message = `if \`${text}\` is a Daytona client, use \`${target}(...)\` (SDK 2.x moved client methods to .v0)`;
      manual(access, message);
    }
  }
  for (const binary of source.getDescendantsOfKind(SyntaxKind.BinaryExpression)) {
    if (binary.getOperatorToken().getKind() !== SyntaxKind.InKeyword) continue;
    const key = unwrap(binary.getLeft());
    if (!Node.isStringLiteral(key) && !Node.isNoSubstitutionTemplateLiteral(key)) continue;
    const member = key.getLiteralValue();
    if (!mapping.movedClientMembers.ts.includes(member)) continue;
    const receiver = binary.getRight();
    if (receivers.client(receiver, member)) {
      for (const message of outside.messages(receiver)) manual(binary, message);
      const grouped = Node.isIdentifier(receiver) || isMemberAccess(receiver) || Node.isParenthesizedExpression(receiver) ||
        Node.isCallExpression(receiver) || Node.isNewExpression(receiver) && receiver.getFirstChildByKind(SyntaxKind.OpenParenToken) !== undefined;
      if (!grouped) edits.push({ start: receiver.getStart(), end: receiver.getStart(), text: '(' });
      edits.push({ start: receiver.getEnd(), end: receiver.getEnd(), text: grouped ? '.v0' : ').v0' });
      v0Receivers.push(receiver);
    } else if (receivers.plausible(receiver, member)) {
      manual(binary, `if \`${displayText(receiver.getText())}\` is a Daytona client, check \`${displayText(key.getText())} in ${displayText(receiver.getText())}.v0\` (SDK 2.x moved client members to .v0)`);
    }
  }
  for (const variable of source.getDescendants().filter(node => Node.isVariableDeclaration(node) || Node.isParameterDeclaration(node))) {
    const binding = variable.getNameNode();
    const value = variable.getInitializer();
    if (!Node.isObjectBindingPattern(binding)) continue;
    if (value && binding.getElements().some(entry => !entry.getDotDotDotToken() && bindingKey(entry) === 'createAxiosInstance') &&
        inheritsStaticHelper(value)) manual(binding, staticHelperMessage);
    const client = Node.isParameterDeclaration(variable) &&
      (annotatedKind(variable, mapping) === 'client' || receivers.client(variable)) || value !== undefined && receivers.client(value);
    if (!client) continue;
    if (binding.getElements().some(entry => mapping.movedClientMembers.ts.includes(bindingKey(entry) ?? ''))) {
      for (const message of outside.messages(value ?? variable)) manual(binding, message);
      manual(binding, 'Review client destructuring manually: SDK 2.x moved client members to .v0; extracted methods need their v0 this receiver.');
    }
  }
  reportConstructorErrors(source, mapping, { manual, v0Receivers });
  for (const [owner, lines] of comments) edits.push(commentEdit(owner, lines));
  return { after: applyEdits(source.getFullText(), edits), items };
}
