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

  /** The 0.x receiver kind (other than the client) a stable binding provably holds: by annotation or an SDK producer call. */
  private objectKind(node: Node, seen: Set<Node> = new Set()): string | undefined {
    const value = unwrap(node);
    if (seen.has(value)) return undefined;
    seen.add(value);
    if (Node.isCallExpression(value)) {
      const callee = unwrap(value.getExpression());
      if (!isMemberAccess(callee)) return undefined;
      const member = memberName(callee);
      const receiver = callee.getExpression();
      const owner = this.root(receiver) ? 'client' : this.objectKind(receiver, seen);
      if (owner === undefined || member === undefined) return undefined;
      const entry = this.mapping.suggestions.find(candidate => candidate.receiver === owner && candidate.v0.ts === member);
      if (!entry) return undefined;
      return Object.entries(this.mapping.receivers).find(([, kind]) => kind.producedBy?.includes(entry.id))?.[0];
    }
    if (isMemberAccess(value)) {
      const name = memberName(value);
      const parent = this.objectKind(value.getExpression(), seen);
      if (parent === undefined || name === undefined || this.writes.lost(value.getExpression(), name)) return undefined;
      return Object.entries(this.mapping.receivers).find(([, kind]) => kind.parent === parent && kind.attr?.ts === name)?.[0];
    }
    if (!Node.isIdentifier(value)) return undefined;
    const definitions = declarations(value);
    if (definitions.length !== 1) return undefined;
    const definition = definitions[0];
    if ((!Node.isVariableDeclaration(definition) && !Node.isParameterDeclaration(definition)) || !bindingIsStable(definition)) return undefined;
    const annotated = annotatedKind(definition, this.mapping);
    if (annotated !== undefined) return annotated === 'client' ? undefined : annotated;
    const init = Node.isVariableDeclaration(definition) ? definition.getInitializer() : undefined;
    return init === undefined ? undefined : this.objectKind(init, seen);
  }

  proven(callee: Node): boolean {
    if (!isMemberAccess(callee)) return false;
    const member = memberName(callee);
    if (member === 'v0') return this.root(callee);
    const receiver = callee.getExpression();
    if (member === undefined) return false;
    if (this.mapping.movedClientMembers.ts.includes(member)) return this.root(receiver) && !this.writes.lost(receiver, member);
    // A 0.x sandbox (or service) method updates the object's data, never the
    // service objects it holds, so later `sandbox.fs.…` calls keep their proof.
    const kind = this.objectKind(receiver);
    return kind !== undefined && this.mapping.suggestions.some(entry => entry.receiver === kind && entry.v0.ts === member) &&
      !this.writes.lost(receiver, member);
  }
}
