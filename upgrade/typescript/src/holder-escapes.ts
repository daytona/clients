/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { Project, SourceFile } from 'ts-morph';
import { unwrap } from './edits.js';
import { isMemberAccess } from './member-access.js';
import { objectIdentity } from './object-identity.js';
import { HolderMethods } from './holder-methods.js';
import type { Mapping } from './model.js';
import { SdkMethods } from './sdk-methods.js';

type MethodSummary = {
  readonly escapes: boolean;
  readonly calls: readonly Node[];
};

type ProjectEffects = {
  readonly sources: ReadonlySet<SourceFile>;
  readonly effects: ReadonlyMap<Node, readonly Node[]>;
};
const effectsByProject = new WeakMap<Project, Map<Mapping | undefined, ProjectEffects>>();

function isEffect(effect: Node): boolean {
  return Node.isCallExpression(effect) || Node.isNewExpression(effect) || Node.isVariableDeclaration(effect) ||
    Node.isVariableStatement(effect) && effect.isExported() || Node.isExportSpecifier(effect) || Node.isExportAssignment(effect) ||
    Node.isPropertyAssignment(effect) || Node.isShorthandPropertyAssignment(effect) || Node.isPropertyDeclaration(effect) ||
    Node.isBinaryExpression(effect) && effect.getOperatorToken().getKind() >= SyntaxKind.FirstAssignment &&
      effect.getOperatorToken().getKind() <= SyntaxKind.LastAssignment;
}

/** Each source-file set shares one inverted index, not one scan per use. */
export class HolderEscapes {
  private readonly exposed = new WeakMap<Node, ReadonlySet<Node>>();
  private readonly captures = new WeakMap<Node, ReadonlySet<Node>>();
  private readonly holders = new Set<Node>();
  private readonly methods = new WeakMap<Node, MethodSummary>();
  private readonly methodEscapes = new WeakMap<Node, boolean>();
  private readonly dispatch = new HolderMethods();
  private readonly sdkMethods: SdkMethods | undefined;
  constructor(private readonly mapping?: Mapping) { this.sdkMethods = mapping ? new SdkMethods(mapping) : undefined; }

  private methodSummary(body: Node): MethodSummary {
    const cached = this.methods.get(body);
    if (cached) return cached;
    const owner = body.getFirstAncestor(node => Node.isClassDeclaration(node) || Node.isClassExpression(node));
    let escapes = false;
    const calls: Node[] = [];
    for (const effect of body.getDescendants()) {
      if (!isEffect(effect)) continue;
      if (owner && this.escaped(effect, false).has(owner)) escapes = true;
      if (!Node.isCallExpression(effect)) continue;
      const callee = unwrap(effect.getExpression());
      if (Node.isPropertyAccessExpression(callee) || Node.isElementAccessExpression(callee)) {
        const receiver = unwrap(callee.getExpression());
        if ((Node.isThisExpression(receiver) || Node.isSuperExpression(receiver)) && objectIdentity(receiver) === owner) calls.push(callee);
      }
    }
    const summary = { escapes, calls };
    this.methods.set(body, summary);
    return summary;
  }

  private callEscapes(callee: Node): boolean {
    const bodies = this.dispatch.targets(callee);
    if (!bodies) return true;
    const cached = this.methodEscapes.get(callee);
    if (cached !== undefined) return cached;
    // Memoize body summaries, but never a partial traversal's false result: a
    // recursive edge may lead back to an escaping caller later in the worklist.
    const pending = bodies.map(body => ({ body, depth: 0 }));
    const seen = new Set<Node>();
    let escapes = false;
    for (let index = 0; index < pending.length; index++) {
      const current = pending[index];
      if (!current || seen.has(current.body)) continue;
      seen.add(current.body);
      if (current.depth >= 8) { escapes = true; break; }
      const summary = this.methodSummary(current.body);
      if (summary.escapes) { escapes = true; break; }
      for (const call of summary.calls) {
        const targets = this.dispatch.targets(call);
        if (!targets) { escapes = true; break; }
        for (const body of targets) pending.push({ body, depth: current.depth + 1 });
      }
      if (escapes) break;
    }
    this.methodEscapes.set(callee, escapes);
    return escapes;
  }

  private identities(node: Node, closures: boolean): ReadonlySet<Node> {
    const value = unwrap(node);
    const cache = closures ? this.captures : this.exposed;
    const cached = cache.get(value);
    if (cached) return cached;
    const own = objectIdentity(value);
    const identities = new Set<Node>(this.holders.has(own) ? [own] : []);
    if (Node.isFunctionLikeDeclaration(value) || Node.isFunctionExpression(value)) {
      if (closures) {
        for (const reference of value.getDescendants()) {
          if (!Node.isIdentifier(reference) && !Node.isThisExpression(reference)) continue;
          const identity = objectIdentity(reference);
          // Capturing a binding solely to read an SDK member doesn't expose
          // the client. Real argument/storage escapes are indexed separately.
          const access = reference.getParent();
          if (access && isMemberAccess(access) && access.getExpression() === reference && this.sdkMethods?.proven(access)) continue;
          // A local created inside the invoked function is not a captured holder.
          if (this.holders.has(identity) && !identity.getAncestors().includes(value)) identities.add(identity);
        }
      }
    } else if (!isMemberAccess(value)) {
      for (const child of value.getChildren()) {
        for (const identity of this.identities(child, closures)) identities.add(identity);
      }
    }
    cache.set(value, identities);
    return identities;
  }

  private escaped(effect: Node, followMethods = true): ReadonlySet<Node> {
    const escaped = new Set<Node>();
    const include = (node: Node, closures: boolean): void => {
      for (const identity of this.identities(node, closures)) escaped.add(identity);
    };
    if (Node.isCallExpression(effect) || Node.isNewExpression(effect)) {
      const callee = unwrap(effect.getExpression());
      const target = objectIdentity(callee);
      include(target, true);
      // Constructing a class doesn't expose an existing instance of that class.
      if (Node.isClassDeclaration(target) || Node.isClassExpression(target)) escaped.delete(target);
      if (Node.isPropertyAccessExpression(callee) || Node.isElementAccessExpression(callee)) {
        const receiver = callee.getExpression();
        const identity = objectIdentity(receiver);
        include(receiver, false);
        // In-class writes already join across methods, but helpers can expose
        // this to outside code. Carry that escape to the invocation's scope.
        if (this.sdkMethods?.proven(callee) ||
            (Node.isThisExpression(unwrap(receiver)) || Node.isSuperExpression(unwrap(receiver))) &&
            (!followMethods || !this.callEscapes(callee))) escaped.delete(identity);
      }
      for (const argument of effect.getArguments()) include(argument, true);
    } else if (Node.isVariableStatement(effect) && effect.isExported()) {
      for (const declaration of effect.getDeclarations()) include(declaration.getNameNode(), false);
    } else if (Node.isExportSpecifier(effect)) {
      if (!effect.isTypeOnly() && !effect.getExportDeclaration().isTypeOnly()) {
        for (const declaration of effect.getLocalTargetDeclarations()) include(declaration, false);
      }
    } else if (Node.isExportAssignment(effect)) {
      include(effect.getExpression(), false);
    } else if (Node.isShorthandPropertyAssignment(effect)) {
      include(effect.getNameNode(), false);
    } else {
      const stored = Node.isBinaryExpression(effect) ? effect.getRight() :
        Node.isVariableDeclaration(effect) || Node.isPropertyAssignment(effect) || Node.isPropertyDeclaration(effect)
          ? effect.getInitializer() : undefined;
      if (stored) {
        include(stored, false);
        // Initial creation isn't an escape; storing an existing alias is.
        escaped.delete(unwrap(stored));
      }
    }
    return escaped;
  }

  index(node: Node): ReadonlyMap<Node, readonly Node[]> {
    const project = node.getProject();
    let files = project.getSourceFiles();
    while (true) {
      const cached = effectsByProject.get(project)?.get(this.mapping);
      if (cached && files.length === cached.sources.size && files.every(file => cached.sources.has(file))) return cached.effects;
      const sources = new Set(files);
      // Rebuild summaries and dispatch too: newly resolved overrides can make a
      // previously pure helper escape. Never reuse a partial build's caches.
      const effects = new HolderEscapes(this.mapping).build(files);
      files = project.getSourceFiles();
      // Type queries inside the build can themselves load more modules.
      if (files.length !== sources.size || !files.every(file => sources.has(file))) continue;
      const mappings = effectsByProject.get(project) ?? new Map<Mapping | undefined, ProjectEffects>();
      mappings.set(this.mapping, { sources, effects });
      effectsByProject.set(project, mappings);
      return effects;
    }
  }

  private build(files: readonly SourceFile[]): ReadonlyMap<Node, readonly Node[]> {
    const index = new Map<Node, Node[]>();
    const sources = files.filter(source => !source.isDeclarationFile())
      .map(source => source.getDescendants());
    this.dispatch.index(sources.flat());
    // Only receivers used by project member accesses can own property proofs.
    // Do not post thousands of irrelevant SDK declaration identities per call.
    for (const nodes of sources) {
      for (const value of nodes) {
        if (isMemberAccess(value)) this.holders.add(objectIdentity(value.getExpression()));
        // A base class may only use this as an argument in an inherited helper.
        if (Node.isClassDeclaration(value) || Node.isClassExpression(value)) this.holders.add(value);
      }
    }
    for (const nodes of sources) {
      for (const effect of nodes) {
        if (!isEffect(effect)) continue;
        for (const identity of this.escaped(effect)) {
          const positions = index.get(identity) ?? [];
          positions.push(effect);
          index.set(identity, positions);
        }
      }
    }
    for (const positions of index.values()) positions.sort((left, right) => left.getStart() - right.getStart());
    return index;
  }
}
