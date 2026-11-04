/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { Project } from 'ts-morph';
import { declarations } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess } from './member-access.js';
import type { Invocation } from './callback-parameter-values.js';

/** Reverse callee dependencies are a candidate filter, never a callable proof.
 * Argument edges retain callback/alias/container reachability; the resolver still
 * selects the exact callback property and correlates its supplying invocation.
 */
export class CallbackCallIndex {
  private readonly uses = new Map<Node, Set<Node>>();
  private readonly calls = new Map<Node, Invocation[]>();
  private readonly watched = new Set<Node>();

  constructor(project: Project) {
    const checker = project.getTypeChecker();
    for (const source of project.getSourceFiles()) {
      if (source.isDeclarationFile() || source.isInNodeModules()) continue;
      for (const call of [...source.getDescendantsOfKind(SyntaxKind.CallExpression),
        ...source.getDescendantsOfKind(SyntaxKind.NewExpression)]) {
        const callee = unwrap(call.getExpression());
        const calls = this.calls.get(callee) ?? [];
        calls.push(call);
        this.calls.set(callee, calls);
        this.watch(callee);
        const target = checker.getResolvedSignature(call)?.getDeclaration();
        if (!target || target.getSourceFile().isDeclarationFile()) continue;
        if (!Node.isFunctionDeclaration(target) && !Node.isFunctionExpression(target) && !Node.isArrowFunction(target) &&
          !Node.isMethodDeclaration(target) && !Node.isConstructorDeclaration(target)) continue;
        const implementation = Node.isFunctionDeclaration(target) || Node.isMethodDeclaration(target) ||
          Node.isConstructorDeclaration(target) ? target.getImplementation() : undefined;
        // Overload signatures and bodies have distinct parameter declarations.
        // Keep both reachable; this index supplies candidates, never proof.
        for (const declaration of new Set([target, implementation ?? target])) {
          for (const [position, parameter] of declaration.getParameters().entries()) {
            const argumentsAtSlot = call.getArguments().slice(position, parameter.isRestParameter() ? undefined : position + 1);
            for (const argument of argumentsAtSlot) this.link(argument, parameter);
          }
        }
      }
      for (const write of source.getDescendantsOfKind(SyntaxKind.BinaryExpression)) {
        if (write.getOperatorToken().getKind() !== SyntaxKind.EqualsToken) continue;
        const left = unwrap(write.getLeft());
        const target = isMemberAccess(left) ? left.getExpression() : left;
        for (const provider of declarations(target)) this.link(write.getRight(), provider);
      }
    }
  }

  private link(provider: Node, use: Node): void {
    const uses = this.uses.get(provider) ?? new Set<Node>();
    uses.add(use);
    this.uses.set(provider, uses);
    this.watch(provider);
  }

  private watch(node: Node): void {
    if (this.watched.has(node) || node.getSourceFile().isDeclarationFile()) return;
    this.watched.add(node);
    const value = unwrap(node);
    if (value !== node) { this.link(value, node); return; }
    if (Node.isIdentifier(value) || isMemberAccess(value)) {
      for (const provider of declarations(value)) this.link(provider, value);
      if (isMemberAccess(value)) this.link(value.getExpression(), value);
      return;
    }
    if (Node.isBindingElement(value)) {
      const owner = value.getParent().getParent();
      this.link(initializer(owner) ?? owner, value);
      return;
    }
    if (Node.isObjectLiteralExpression(value)) {
      for (const property of value.getProperties()) this.link(property, value);
      return;
    }
    if (Node.isShorthandPropertyAssignment(value)) { this.link(value.getNameNode(), value); return; }
    if (Node.isConditionalExpression(value)) {
      this.link(value.getWhenTrue(), value);
      this.link(value.getWhenFalse(), value);
      return;
    }
    const init = Node.isParameterDeclaration(value) ? value.getInitializer() : initializer(value);
    if (init) this.link(init, value);
    if (Node.isClassDeclaration(value) || Node.isClassExpression(value)) {
      for (const constructor of value.getConstructors()) this.link(constructor, value);
    }
  }

  candidates(owner: Node, step: () => boolean): readonly Invocation[] {
    const pending = [owner];
    const seen = new Set<Node>();
    const calls = new Set<Invocation>();
    for (const value of pending) {
      if (seen.has(value)) continue;
      if (!step()) break;
      seen.add(value);
      for (const call of this.calls.get(value) ?? []) calls.add(call);
      pending.push(...this.uses.get(value) ?? []);
    }
    return [...calls];
  }
}
