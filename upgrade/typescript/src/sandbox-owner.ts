/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, ts } from 'ts-morph';
import { declarations } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { bindingIsStable } from './stable-bindings.js';
import type { Receivers } from './receivers.js';

export function sandboxOwner(node: Node, use: Node, receivers: Receivers): Node | undefined {
  const seen = new Set<Node>();
  const owner = (expression: Node): Node | undefined => {
    const value = unwrap(expression);
    if (seen.has(value)) return undefined;
    seen.add(value);
    if (receivers.kind(value) === 'sandbox') {
      const sameFunction = value.getFirstAncestor(part => ts.isFunctionLike(part.compilerNode)) ===
        use.getFirstAncestor(part => ts.isFunctionLike(part.compilerNode));
      if (!sameFunction && [value, ...value.getDescendants()].some(part => part.getKindName() === 'ThisKeyword')) return undefined;
      const visible = use.getSymbolsInScope(ts.SymbolFlags.Value);
      const accessible = [value, ...value.getDescendants()].filter(Node.isIdentifier).every(identifier => {
        if (Node.isPropertyAccessExpression(identifier.getParent()) && identifier.getParent().getLastChild() === identifier) return true;
        const symbol = identifier.getSymbol();
        return symbol === undefined || visible.includes(symbol);
      });
      return accessible ? expression : undefined;
    }
    // A handle a sandbox call produced (`sandbox.process.createPty(...)`) is
    // owned by that call's sandbox; a new sandbox (a fork) is not.
    if (Node.isCallExpression(value)) {
      const callee = value.getExpression();
      const produced = receivers.suggestion(value);
      const handle = produced && Object.entries(receivers.mapping.receivers)
        .some(([kind, receiver]) => kind !== 'sandbox' && receiver.producedBy?.includes(produced.id));
      if (handle && isMemberAccess(callee)) return owner(callee.getExpression());
    }
    if (isMemberAccess(value)) {
      const kind = receivers.kind(value);
      if (kind && receivers.mapping.receivers[kind]?.attr?.ts === memberName(value)) {
        const result = owner(value.getExpression());
        if (result) return result;
      }
    }
    for (const declaration of declarations(value)) {
      if (!bindingIsStable(declaration) || declaration.getSourceFile() !== use.getSourceFile()) continue;
      const init = initializer(declaration);
      if (init) {
        const result = owner(init);
        if (result) return result;
      }
    }
    return undefined;
  };
  return owner(node);
}
