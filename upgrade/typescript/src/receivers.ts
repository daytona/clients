/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { Mapping, Suggestion } from './model.js';
import { assertNever } from './model.js';
import { annotatedKind, constructorMayReplaceInstance, declarations, isClientConstructor, isConstructor, typeKind } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';
import { isMemberAccess, memberName } from './member-access.js';
import { hasReceiverHint } from './receiver-hints.js';
import { importedReceiver } from './imported-receivers.js';
import { ReceiverPackages } from './receiver-packages.js';
import { externalReceiverKind, unresolvedType } from './external-receiver-kind.js';
import { isV0Object } from './v0-object.js';
import { ambiguousObjectMember, ambiguousObjectProperty } from './object-member-proof.js';
import { receiverConstraints } from './receiver-constraints.js';
import { PropertyProofs } from './property-proofs.js';
import { MemberWrites } from './member-writes.js';

export class Receivers {
  private readonly packages = new ReceiverPackages();
  private readonly properties: PropertyProofs;
  private readonly members: MemberWrites;
  // Analysis reads an immutable project; discard these proofs with this receiver analyzer.
  private readonly v0Objects = new WeakMap<Node, boolean>();
  constructor(readonly mapping: Mapping, readonly workspaceNames: readonly string[] = []) {
    this.properties = new PropertyProofs(mapping);
    this.members = new MemberWrites(mapping);
  }

  unresolved(node: Node): boolean {
    return this.unstable(node) || unresolvedType(node.getType());
  }
  private v0Object(node: Node): boolean {
    const cached = this.v0Objects.get(node);
    if (cached !== undefined) return cached;
    // An explicit namespace is never a root receiver, even without SDK types.
    let access = unwrap(node, true);
    while (isMemberAccess(access)) {
      if (memberName(access) === 'v0') {
        this.v0Objects.set(node, true);
        return true;
      }
      access = unwrap(access.getExpression(), true);
    }
    const proven = isV0Object(node);
    this.v0Objects.set(node, proven);
    return proven;
  }
  private unstable(node: Node, seen: Set<Node> = new Set()): boolean {
    const value = unwrap(node);
    if (seen.has(value)) return false;
    seen.add(value);
    if (constructorMayReplaceInstance(value) || ambiguousObjectMember(value) || this.properties.lost(value)) return true;
    const own = declarations(Node.isPropertyAccessExpression(value) ? value.getNameNode() : value).some(declaration => {
      if (ambiguousObjectProperty(declaration)) return true;
      if (!bindingIsStable(declaration)) return true;
      if (Node.isClassDeclaration(declaration) || Node.isClassExpression(declaration)) {
        const base = declaration.getExtends()?.getExpression();
        return constructorMayReplaceInstance(declaration) || base !== undefined && this.unstable(base, seen);
      }
      const init = initializer(declaration);
      const expression = init ? unwrap(init) : undefined;
      return expression !== undefined && (Node.isIdentifier(expression) || Node.isNewExpression(expression) || Node.isClassExpression(expression)) && this.unstable(expression, seen);
    });
    return own || (isMemberAccess(value) || Node.isNewExpression(value)) && this.unstable(value.getExpression(), seen);
  }
  private memberOrigin(node: Node, member: string): 'sdk' | 'other' | 'ambiguous' | 'missing' {
    const providers = receiverConstraints(node.getType()).flatMap(type => type.getProperty(member)?.getDeclarations() ?? []);
    if (!providers.length) return 'missing';
    const sdk = providers.map(provider => provider.getFirstAncestorByKind(SyntaxKind.ClassDeclaration)?.getName() === 'Daytona' &&
      this.packages.version(provider.getSourceFile().getFilePath()) === 0);
    return sdk.every(Boolean) ? 'sdk' : sdk.some(Boolean) ? 'ambiguous' : 'other';
  }
  ambiguous(node: Node, member: string): boolean {
    const origin = this.memberOrigin(node, member);
    return origin === 'ambiguous' || origin === 'sdk' && !this.client(node, member);
  }

  plausible(node: Node, member?: string): boolean {
    const value = unwrap(node, true);
    if (this.v0Object(value)) return false;
    if (['v0', 'v2'].includes(memberName(value) ?? '')) return false;
    if (member && this.members.lost(value, member) && this.client(value)) return true;
    const kind = this.kind(value);
    if (kind !== undefined && kind !== 'client') return false;
    if (this.unstable(value) && this.syntactic(value, new Set(), true) === 'client') return true;
    if (this.client(value, member) || (member ? this.ambiguous(value, member) :
      this.mapping.movedClientMembers.ts.some(name => this.ambiguous(value, name)))) return true;
    if (value.getKind() === SyntaxKind.SuperKeyword) {
      const owner = value.getAncestors().find(node => Node.isClassDeclaration(node) || Node.isClassExpression(node));
      const base = owner?.getExtends()?.getExpression();
      return base !== undefined && isClientConstructor(base, this.mapping);
    }
    const service = isMemberAccess(value) && this.client(value.getExpression()) &&
      Object.values(this.mapping.receivers).some(entry => entry.parent === 'client' && entry.attr?.ts === memberName(value));
    return !service && kind === undefined && (this.unresolved(value) || importedReceiver(value) !== undefined || /daytona/i.test(value.getText())) &&
      hasReceiverHint(value, this.mapping, this.workspaceNames);
  }

  client(node: Node, member?: string): boolean {
    const value = unwrap(node, true);
    if (this.v0Object(value)) return false;
    if (this.unstable(value)) return false;
    if (['v0', 'v2'].includes(memberName(value) ?? '')) return false;
    if (member) {
      const overrides = declarations(value).some(declaration => {
        const init = initializer(declaration);
        const concrete = init && unwrap(init);
        return Node.isNewExpression(concrete) && isClientConstructor(concrete.getExpression(), this.mapping) &&
          concrete.getType().getProperty(member)?.getDeclarations().some(provider =>
            this.packages.packageRoot(provider.getSourceFile().getFilePath()) === undefined);
      });
      if (overrides) return false;
      if (this.members.lost(value, member)) return false;
      const origin = this.memberOrigin(value, member);
      switch (origin) {
        case 'sdk': break;
        case 'ambiguous': return false;
        case 'other': if (Node.isThisExpression(value) || receiverConstraints(value.getType()).some(type => !unresolvedType(type))) return false; break;
        case 'missing': break;
        default: assertNever(origin);
      }
    }
    const identity = this.packages.identity(value, true);
    if (identity) return identity.name === 'Daytona' && (identity.major === 0 ||
      (identity.major >= 2 && !identity.path.includes('/v0/') && !identity.path.includes('/v2/') &&
      (member === undefined || value.getType().getProperty(member) === undefined)));
    const type = value.getType().getNonNullableType();
    const subclass = type.getSymbol()?.getDeclarations().some(declaration => {
      if (!Node.isClassDeclaration(declaration) && !Node.isClassExpression(declaration)) return false;
      if (constructorMayReplaceInstance(declaration)) return false;
      const base = declaration.getExtends()?.getExpression();
      return base !== undefined && isClientConstructor(base, this.mapping);
    });
    if (subclass) return true;
    if (!type.isAny() && !type.isUnknown() && type.getProperties().length > 0) {
      // A resolved non-client (notably Sandbox) must never gain .v0.
      const symbol = type.getSymbol();
      if (symbol && symbol.getName() !== 'Daytona') return false;
    }
    // Source that already chose .v0 can retain syntactic provenance for suggestions without adding a namespace.
    const parent = value.getParent();
    const explicitV0 = parent !== undefined && isMemberAccess(parent) && parent.getExpression() === value && memberName(parent) === 'v0';
    return !(this.unresolved(value) && importedReceiver(value) && !explicitV0) && this.syntactic(value, new Set()) === 'client';
  }
  private syntactic(node: Node, seen: Set<Node>, allowReplacement = false): string | undefined {
    const awaited = Node.isAwaitExpression(unwrap(node, true));
    const value = unwrap(node);
    if (seen.has(value)) return undefined;
    seen.add(value);
    if (isConstructor(value, this.mapping, allowReplacement)) return 'client';
    if (Node.isCallExpression(value)) {
      for (const declaration of declarations(value.getExpression())) {
        if (Node.isFunctionDeclaration(declaration) || Node.isMethodDeclaration(declaration) || Node.isArrowFunction(declaration)) {
          const annotation = declaration.getReturnTypeNode();
          if (annotation) {
            const result = Node.isTypeReference(annotation) && annotation.getTypeName().getText() === 'Promise' ? annotation.getTypeArguments()[0] : annotation;
            if (result && typeKind(result, this.mapping) === 'client') return 'client';
          }
        }
      }
    }
    if (Node.isNewExpression(value) || Node.isCallExpression(value)) {
      const external = externalReceiverKind(value, this.packages);
      if (external) return external;
    }
    for (const declaration of declarations(Node.isPropertyAccessExpression(value) ? value.getNameNode() : value)) {
      if (ambiguousObjectProperty(declaration)) continue;
      if (!bindingIsStable(declaration)) continue;
      const annotated = annotatedKind(declaration, this.mapping);
      if (annotated) return annotated;
      if (awaited && (Node.isVariableDeclaration(declaration) || Node.isParameterDeclaration(declaration) || Node.isPropertyDeclaration(declaration))) {
        const annotation = declaration.getTypeNode();
        const argument = Node.isTypeReference(annotation) && annotation.getTypeName().getText() === 'Promise' ? annotation.getTypeArguments()[0] : undefined;
        if (argument) {
          const kind = typeKind(argument, this.mapping);
          if (kind) return kind;
        }
      }
      const init = initializer(declaration);
      if (init) {
        const kind = this.syntactic(init, seen, allowReplacement);
        if (kind) return kind;
      }
      if (Node.isPropertyAccessExpression(declaration)) {
        const assignment = declaration.getParent();
        if (Node.isBinaryExpression(assignment) && assignment.getLeft() === declaration) {
          const kind = this.syntactic(assignment.getRight(), seen, allowReplacement);
          if (kind) return kind;
        }
      }
      if (Node.isPropertyDeclaration(declaration)) {
        const owner = declaration.getFirstAncestorByKind(SyntaxKind.ClassDeclaration);
        for (const binary of owner?.getDescendantsOfKind(SyntaxKind.BinaryExpression) ?? []) {
          if (binary.getLeft().getText() === `this.${declaration.getName()}`) {
            const kind = this.syntactic(binary.getRight(), seen, allowReplacement);
            if (kind) return kind;
          }
        }
      }
    }
    return undefined;
  }
  kind(node: Node, seen: Set<Node> = new Set()): string | undefined {
    const value = unwrap(node, true);
    if (this.unstable(value)) return undefined;
    if (seen.has(value)) return undefined;
    seen.add(value);
    if (isMemberAccess(value)) {
      const name = memberName(value);
      if (name === 'v2') return undefined;
      if (name === 'v0' && this.client(value.getExpression())) return 'client';
      const parent = this.client(value.getExpression(), name) ? 'client' : this.kind(value.getExpression(), seen);
      for (const [kind, receiver] of Object.entries(this.mapping.receivers)) {
        if (receiver.parent === parent && receiver.attr?.ts === name) return kind;
      }
    }
    if (this.client(value)) return 'client';
    const identity = this.packages.identity(value);
    if (identity && (identity.major === 0 || identity.path.includes('/v0/'))) {
      for (const [kind, receiver] of Object.entries(this.mapping.receivers)) {
        if (receiver.ts.types.includes(identity.name)) return kind;
      }
    }
    if (this.unresolved(value) && importedReceiver(value)) return undefined;
    // Preserve receiver-name evidence through initializer/alias traversal.
    const syntactic = this.syntactic(value, new Set([...seen].filter(node => node !== value)));
    if (syntactic && syntactic !== 'client') return syntactic;
    if (Node.isCallExpression(unwrap(value))) {
      const suggestion = this.suggestion(unwrap(value), seen);
      if (suggestion) return Object.entries(this.mapping.receivers).find(([, receiver]) => receiver.producedBy?.includes(suggestion.id))?.[0];
    }
    for (const declaration of declarations(Node.isPropertyAccessExpression(value) ? value.getNameNode() : value)) {
      if (ambiguousObjectProperty(declaration)) continue;
      if (!bindingIsStable(declaration)) continue;
      const init = initializer(declaration);
      if (init) {
        const kind = this.kind(init, seen);
        if (kind) return kind;
      }
      const loop = declaration.getFirstAncestorByKind(SyntaxKind.ForOfStatement);
      if (loop) return this.kind(loop.getExpression(), seen);
    }
    return undefined;
  }
  suggestion(call: Node, seen: Set<Node> = new Set()): Suggestion | undefined {
    if (!Node.isCallExpression(call)) return undefined;
    const access = call.getExpression();
    if (!isMemberAccess(access)) return undefined;
    const method = memberName(access);
    const receiver = this.client(access.getExpression(), method) ? 'client' : this.kind(access.getExpression(), seen);
    return this.mapping.suggestions.find(entry => entry.receiver === receiver && entry.v0.ts === method);
  }
}
