/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import type { ClassDeclaration, ClassExpression } from 'ts-morph';
import { declarations } from './bindings.js';
import { propertyKey } from './binding-key.js';
import { unwrap } from './edits.js';
import { memberName } from './member-access.js';
import { objectIdentity } from './object-identity.js';

type ClassNode = ClassDeclaration | ClassExpression;

function isClass(node: Node): node is ClassNode {
  return Node.isClassDeclaration(node) || Node.isClassExpression(node);
}

function methodBodies(members: readonly Node[]): readonly Node[] | undefined {
  const bodies: Node[] = [];
  for (const member of members) {
    if (member.getSourceFile().isDeclarationFile()) return undefined;
    const value = Node.isPropertyDeclaration(member) ? member.getInitializer() : member;
    if (!Node.isMethodDeclaration(value) && !Node.isArrowFunction(value) && !Node.isFunctionExpression(value)) return undefined;
    const body = value.getBody();
    if (body) bodies.push(body);
  }
  return bodies.length ? bodies : undefined;
}

/** Dispatch targets are immutable and memoized for the lifetime of one analysis. */
export class HolderMethods {
  private readonly children = new Map<ClassNode, ClassNode[]>();
  private readonly descendants = new WeakMap<ClassNode, readonly ClassNode[]>();
  private readonly calls = new WeakMap<Node, readonly Node[] | undefined>();
  private readonly unresolvedBases = new Set<ClassNode>();
  private readonly bases = new Map<ClassNode, ClassNode>();
  private readonly members = new Map<ClassNode, ReadonlyMap<string, readonly Node[]>>();
  private readonly opaque = new Set<ClassNode>();
  private readonly implementations = new WeakMap<ClassNode, Map<string, readonly Node[] | undefined>>();

  index(nodes: readonly Node[]): void {
    for (const child of nodes.filter(isClass)) {
      const members = new Map<string, Node[]>();
      if (child.getDecorators().length) this.opaque.add(child);
      for (const member of child.getInstanceMembers()) {
        const name = propertyKey(member);
        if (name === undefined) { this.opaque.add(child); continue; }
        const declarations = members.get(name) ?? [];
        declarations.push(member);
        members.set(name, declarations);
      }
      this.members.set(child, members);
      const extension = child.getExtends();
      if (!extension) continue;
      const bases = child.getBaseTypes().flatMap(base => base.getSymbol()?.getDeclarations().filter(isClass) ?? []);
      if (bases.length === 1 && bases[0]) this.bases.set(child, bases[0]);
      else this.unresolvedBases.add(child);
      // An opaque mixin can still name a project ancestor. Attach that class
      // conservatively, without poisoning unrelated hierarchies.
      const ancestors = this.unresolvedBases.has(child)
        ? [...bases, ...[extension.getExpression(), ...extension.getDescendants()]
          .filter(Node.isIdentifier).flatMap(node => declarations(node).filter(isClass))]
        : bases;
      for (const base of new Set(ancestors)) {
        const children = this.children.get(base) ?? [];
        children.push(child);
        this.children.set(base, children);
      }
    }
  }

  private implementation(owner: ClassNode, name: string, seen: ReadonlySet<ClassNode> = new Set()): readonly Node[] | undefined {
    const cached = this.implementations.get(owner) ?? new Map<string, readonly Node[] | undefined>();
    if (cached.has(name)) return cached.get(name);
    if (this.opaque.has(owner) || seen.has(owner)) return undefined;
    const members = this.members.get(owner)?.get(name);
    const base = this.bases.get(owner);
    // A project declaration wins before an unresolved ambient ancestor. Only
    // lookup that would continue through the opaque base loses its proof.
    const bodies = members ? methodBodies(members) : this.unresolvedBases.has(owner) ? undefined : base
      ? this.implementation(base, name, new Set(seen).add(owner))
      : methodBodies(owner.getType().getProperty(name)?.getDeclarations() ?? []);
    cached.set(name, bodies);
    this.implementations.set(owner, cached);
    return bodies;
  }

  private receivers(owner: ClassNode): readonly ClassNode[] {
    const cached = this.descendants.get(owner);
    if (cached) return cached;
    const seen = new Set<ClassNode>();
    const pending = [owner];
    for (let index = 0; index < pending.length; index++) {
      const current = pending[index];
      if (!current || seen.has(current)) continue;
      seen.add(current);
      pending.push(...this.children.get(current) ?? []);
    }
    const receivers = [...seen];
    this.descendants.set(owner, receivers);
    return receivers;
  }

  targets(callee: Node): readonly Node[] | undefined {
    if (this.calls.has(callee)) return this.calls.get(callee);
    const targets = this.resolve(callee);
    this.calls.set(callee, targets);
    return targets;
  }

  private resolve(callee: Node): readonly Node[] | undefined {
    const name = memberName(callee);
    if (name === undefined || (!Node.isPropertyAccessExpression(callee) && !Node.isElementAccessExpression(callee))) return undefined;
    const receiver = unwrap(callee.getExpression());
    const declared = Node.isPropertyAccessExpression(callee) ? callee.getNameNode() : callee;
    const bodies = methodBodies(declarations(declared));
    if (!bodies) return undefined;
    // super chooses the base implementation, not an override of that member.
    // Virtual this calls inside its body are still expanded independently.
    if (Node.isSuperExpression(receiver)) return bodies;
    const owner = objectIdentity(receiver);
    if (!isClass(owner)) return undefined;
    const targets = new Set(bodies);
    for (const candidate of this.receivers(owner)) {
      const implementations = this.implementation(candidate, name);
      if (!implementations) return undefined;
      for (const body of implementations) targets.add(body);
    }
    // Exported classes retain the existing project-only policy: hypothetical
    // overrides in unscanned downstream packages are not enumerated here.
    return [...targets];
  }
}
