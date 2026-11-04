/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Mapping, ItemKind } from './model.js';
import type { Receivers } from './receivers.js';
import { declarations, importedName, isClientConstructor } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { objectIdentity, projectCalls, mayPrecede } from './object-identity.js';
import { reflectedWrite } from './reflected-writes.js';
import { memberWriteEffects } from './member-write-effects.js';

function prototypeConstructor(node: Node, mapping: Mapping, seen: Set<Node> = new Set()): Node | undefined {
  const value = unwrap(node);
  if (seen.has(value)) return undefined;
  seen.add(value);
  if (isMemberAccess(value) && memberName(value) === 'prototype') {
    const constructor = value.getExpression();
    return isClientConstructor(constructor, mapping) ? constructor : undefined;
  }
  for (const declaration of declarations(value)) {
    const init = initializer(declaration);
    const constructor = init && prototypeConstructor(init, mapping, seen);
    if (constructor) return constructor;
  }
  return undefined;
}

function inheritsPrototype(node: Node, constructor: Node, seen: Set<Node> = new Set()): boolean {
  const identity = objectIdentity(node);
  if (identity === objectIdentity(constructor)) return true;
  if (seen.has(identity)) return false;
  seen.add(identity);
  if (Node.isNewExpression(identity)) return inheritsPrototype(identity.getExpression(), constructor, seen);
  if (Node.isClassDeclaration(identity) || Node.isClassExpression(identity)) {
    const base = identity.getExtends()?.getExpression();
    return base !== undefined && inheritsPrototype(base, constructor, seen);
  }
  return false;
}

export class MemberWrites {
  constructor(private readonly mapping: Mapping) {}

  lost(receiver: Node, member: string): boolean {
    const identity = objectIdentity(receiver);
    return memberWriteEffects(receiver).some(write => {
      if (write.members !== undefined && !write.members.includes(member) || !mayPrecede(write.effect, receiver)) return false;
      if (objectIdentity(write.target) === identity) return true;
      const constructor = prototypeConstructor(write.target, this.mapping);
      if (!constructor) return false;
      if (importedName(constructor, this.mapping) === 'Daytona') return true;
      return inheritsPrototype(identity, constructor) ||
        receiver.getType().getSymbol()?.getDeclarations().some(declaration => inheritsPrototype(declaration, constructor)) === true;
    });
  }
}

export function reportMemberWrites(source: SourceFile, receivers: Receivers,
  manual: (node: Node, message: string, kind?: ItemKind) => void): void {
  for (const call of projectCalls(source).filter(call => call.getSourceFile() === source)) {
    const write = reflectedWrite(call);
    if (!write || write.members !== undefined && !write.members.some(name => receivers.mapping.movedClientMembers.ts.includes(name))) continue;
    if (prototypeConstructor(write.target, receivers.mapping) || receivers.client(write.target) || receivers.plausible(write.target)) {
      manual(call, 'Review reflective write to a moved root-client member; patch the v0 client manually.', 'mock-target');
    }
  }
}
