/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Edit } from './model.js';
import type { Receivers } from './receivers.js';
import { importedName, typeKind } from './bindings.js';
import { displayText } from './edits.js';

export function typeMembers(source: SourceFile, receivers: Receivers, manual: (node: Node, message: string) => void): readonly Edit[] {
  const edits: Edit[] = [];
  const mapping = receivers.mapping;
  const review = (node: Node, receiver: Node): void => {
    const sdkType = receiver.getDescendants().some(node => Node.isTypeQuery(node) && importedName(node.getExprName(), mapping) === 'Daytona');
    const value = Node.isTypeQuery(receiver) ? receiver.getExprName() :
      Node.isTypeReference(receiver) ? receiver.getTypeName() : receiver;
    if ((sdkType || receivers.plausible(value)) && receivers.unresolved(receiver)) {
      manual(node, `Review type-level client member \`${displayText(node.getText())}\` manually; SDK 2.x moved client members to .v0.`);
    }
  };
  for (const name of source.getDescendantsOfKind(SyntaxKind.QualifiedName)) {
    if (!name.getFirstAncestorByKind(SyntaxKind.TypeQuery)) continue;
    const member = name.getRight().getText();
    if (!mapping.movedClientMembers.ts.includes(member)) continue;
    const receiver = name.getLeft();
    if ([receiver, ...receiver.getDescendants()].some(node => Node.isQualifiedName(node) && ['v0', 'v2'].includes(node.getRight().getText()))) continue;
    if (Node.isQualifiedName(receiver) && Object.values(mapping.receivers).some(entry =>
      entry.parent === 'client' && entry.attr?.ts === receiver.getRight().getText()) && receivers.client(receiver.getLeft())) continue;
    if (importedName(receiver, mapping) !== 'Daytona' && receivers.client(receiver, member)) {
      edits.push({ start: receiver.getEnd(), end: receiver.getEnd(), text: '.v0' });
    } else review(name, receiver);
  }
  for (const access of source.getDescendantsOfKind(SyntaxKind.IndexedAccessType)) {
    const index = access.getIndexTypeNode();
    const literal = Node.isLiteralTypeNode(index) ? index.getLiteral() : undefined;
    const receiver = access.getObjectTypeNode();
    const query = Node.isTypeQuery(receiver) ? receiver.getExprName() : undefined;
    if (query && [query, ...query.getDescendants()].some(node => Node.isQualifiedName(node) &&
      ['v0', 'v2'].includes(node.getRight().getText()))) continue;
    // The qualified-name pass routes the owning client, not the service's indexed member.
    if (Node.isQualifiedName(query) && Object.values(mapping.receivers).some(entry =>
      entry.parent === 'client' && entry.attr?.ts === query.getRight().getText()) &&
      receivers.client(query.getLeft(), query.getRight().getText())) continue;
    if ([receiver, ...receiver.getDescendants()].some(node => {
      if (!Node.isIndexedAccessTypeNode(node)) return false;
      const key = node.getIndexTypeNode();
      const value = Node.isLiteralTypeNode(key) ? key.getLiteral() : undefined;
      return Node.isStringLiteral(value) && ['v0', 'v2'].includes(value.getLiteralValue());
    })) continue;
    if (Node.isIndexedAccessTypeNode(receiver)) {
      const key = receiver.getIndexTypeNode();
      const service = Node.isLiteralTypeNode(key) ? key.getLiteral() : undefined;
      if (Node.isStringLiteral(service) && Object.values(mapping.receivers).some(entry =>
        entry.parent === 'client' && entry.attr?.ts === service.getLiteralValue()) &&
        (typeKind(receiver.getObjectTypeNode(), mapping) === 'client' ||
          receivers.client(receiver.getObjectTypeNode(), service.getLiteralValue()))) continue;
    }
    if (!Node.isStringLiteral(literal)) {
      if (typeKind(receiver, mapping) === 'client' || receivers.client(receiver)) {
        manual(access, `Review type-level client member \`${displayText(access.getText())}\` manually; its indexed key cannot be routed safely to .v0.`);
      }
      continue;
    }
    if (!mapping.movedClientMembers.ts.includes(literal.getLiteralValue())) continue;
    const argument = Node.isTypeReference(receiver) ? receiver.getTypeArguments()[0] : undefined;
    const instance = Node.isTypeReference(receiver) && receiver.getTypeName().getText() === 'InstanceType' &&
      receiver.getTypeName().getSymbol()?.getDeclarations().every(declaration =>
        source.getProject().getProgram().compilerObject.isSourceFileDefaultLibrary(declaration.getSourceFile().compilerNode)) &&
      Node.isTypeQuery(argument) && importedName(argument.getExprName(), mapping) === 'Daytona';
    if (typeKind(receiver, mapping) === 'client' || instance || receivers.client(receiver, literal.getLiteralValue()) ||
      query && importedName(query, mapping) !== 'Daytona' && receivers.client(query, literal.getLiteralValue())) {
      edits.push({ start: receiver.getEnd(), end: receiver.getEnd(), text: "['v0']" });
    } else review(access, receiver);
  }
  return edits;
}
