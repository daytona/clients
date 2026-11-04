/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { annotatedKind, declarations, importedName } from './bindings.js';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { MemberWrites } from './member-writes.js';
import type { Mapping } from './model.js';
import { bindingIsStable } from './stable-bindings.js';
import { isSdkV0Access } from './sdk-v0-access.js';

/** SDK methods preserve their client's own service objects; user methods need not. */
export class SdkMethods {
  private readonly writes: MemberWrites;
  constructor(private readonly mapping: Mapping) { this.writes = new MemberWrites(mapping); }

  private root(node: Node, seen: Set<Node> = new Set()): boolean {
    const value = unwrap(node);
    if (seen.has(value)) return false;
    seen.add(value);
    if (Node.isNewExpression(value)) return importedName(value.getExpression(), this.mapping) === 'Daytona';
    if (!Node.isIdentifier(value) && !isMemberAccess(value)) return false;
    const definitions = declarations(Node.isPropertyAccessExpression(value) ? value.getNameNode() : value);
    if (isMemberAccess(value) && memberName(value) === 'v0') {
      const client = value.getExpression();
      return (isSdkV0Access(value) || definitions.length === 0) && this.root(client, seen) && !this.writes.lost(client, 'v0');
    }
    if (definitions.length !== 1) return false;
    const definition = definitions[0];
    if ((!Node.isVariableDeclaration(definition) && !Node.isParameterDeclaration(definition) &&
        !Node.isPropertyDeclaration(definition) && !Node.isPropertySignature(definition)) || !bindingIsStable(definition)) return false;
    if (annotatedKind(definition, this.mapping) === 'client') return true;
    if (Node.isPropertySignature(definition)) return false;
    const init = definition.getInitializer();
    return (Node.isVariableDeclaration(definition) || Node.isPropertyDeclaration(definition)) &&
      init !== undefined && this.root(init, seen);
  }

  proven(callee: Node): boolean {
    if (!isMemberAccess(callee)) return false;
    const member = memberName(callee);
    if (member === 'v0') return this.root(callee);
    const receiver = callee.getExpression();
    return member !== undefined && this.mapping.movedClientMembers.ts.includes(member) &&
      this.root(receiver) && !this.writes.lost(receiver, member);
  }
}
