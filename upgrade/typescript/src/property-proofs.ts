/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { objectIdentity, mayPrecede } from './object-identity.js';
import { annotatedKind, declarations, isConstructor, isNamespace } from './bindings.js';
import { unresolvedType } from './external-receiver-kind.js';
import { HolderEscapes } from './holder-escapes.js';
import { ReceiverPackages } from './receiver-packages.js';
import type { Mapping } from './model.js';

function functionScope(node: Node): Node | undefined {
  return node.getFirstAncestor(Node.isFunctionLikeDeclaration);
}

export class PropertyProofs {
  private effects: ReadonlyMap<Node, readonly Node[]> | undefined;
  private escaped = new WeakMap<Node, boolean>();
  private readonly holders: HolderEscapes;
  private readonly packages = new ReceiverPackages();
  constructor(private readonly mapping: Mapping) { this.holders = new HolderEscapes(mapping); }

  private typedField(value: Node): boolean {
    const name = Node.isPropertyAccessExpression(value) ? value.getNameNode() : value;
    return declarations(name).some(declaration => {
      if (!Node.isPropertyDeclaration(declaration) && !Node.isParameterDeclaration(declaration) && !Node.isPropertySignature(declaration)) return false;
      if (annotatedKind(declaration, this.mapping) === 'client') {
        return declaration.isReadonly() || !unresolvedType(declaration.getType());
      }
      if (!declaration.isReadonly()) return false;
      const init = initializer(declaration);
      return init !== undefined && isConstructor(init, this.mapping) ||
        this.packages.identity(declaration, true)?.name === 'Daytona';
    });
  }

  lost(node: Node): boolean {
    const value = unwrap(node);
    // SDK exports carry import provenance, not holder instance-property proofs.
    if (!isMemberAccess(value) || isNamespace(value.getExpression(), this.mapping)) return false;
    const identity = objectIdentity(value.getExpression());
    // Explicit SDK namespaces follow a client's identity, not a holder-field proof.
    if (['v0', 'v2'].includes(memberName(value) ?? '') && isConstructor(identity, this.mapping)) return false;
    const instance = Node.isClassDeclaration(identity) || Node.isClassExpression(identity);
    if (this.typedField(value)) return false;
    const effects = this.holders.index(node);
    if (this.effects !== effects) {
      this.effects = effects;
      this.escaped = new WeakMap();
    }
    const cached = this.escaped.get(value);
    if (cached !== undefined) return cached;
    const lost = (this.effects.get(identity) ?? []).some(effect => instance
      ? effect.getSourceFile() === value.getSourceFile() && functionScope(effect) === functionScope(value) &&
        mayPrecede(effect, value)
      : mayPrecede(effect, value));
    this.escaped.set(value, lost);
    return lost;
  }
}
