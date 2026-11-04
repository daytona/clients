/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Node, ParameterDeclaration, Project } from 'ts-morph';
import type { Invocation, SuppliedValue } from './callback-parameter-values.js';
import { CallbackCallIndex } from './callback-call-index.js';

type Context = { readonly calls: readonly Invocation[]; readonly seen: ReadonlySet<Node> };
type Values = readonly SuppliedValue[] | undefined;

/** Shared only by analyses of the same immutable project snapshot. */
class CallIndex {
  readonly callees: CallbackCallIndex;
  readonly values = new Map<string, readonly SuppliedValue[]>();
  private readonly ids = new Map<Node, number>();

  constructor(project: Project) {
    this.callees = new CallbackCallIndex(project);
  }

  key(parameter: ParameterDeclaration, context: Context): string {
    const id = (node: Node): number => {
      const existing = this.ids.get(node);
      if (existing !== undefined) return existing;
      const next = this.ids.size;
      this.ids.set(node, next);
      return next;
    };
    // Invocation order and recursion exclusions affect resolution. Neither may
    // be conflated across callers of the same factory/callback parameter.
    return `${id(parameter)}:${context.calls.map(id).join(',')}:${[...context.seen].map(id).sort((a, b) => a - b).join(',')}`;
  }
}

const indexes = new WeakMap<Project, CallIndex>();

/** One fuel tank for the whole recursive search, not one per branch. */
export class CallbackAnalysis {
  private readonly index: CallIndex;
  private readonly pending = new Map<string, Values>();
  private remaining = 10_000;
  private complete = true;

  constructor(project: Project) {
    const existing = indexes.get(project);
    this.index = existing ?? new CallIndex(project);
    if (!existing) indexes.set(project, this.index);
  }

  candidates(owner: Node): readonly Invocation[] { return this.index.callees.candidates(owner, () => this.step()); }

  enter(node: Node, seen: ReadonlySet<Node>): boolean {
    if (seen.has(node)) return false;
    if (seen.size >= 64) { this.complete = false; return false; }
    return this.step();
  }

  step(): boolean {
    if (this.remaining === 0) { this.complete = false; return false; }
    this.remaining--;
    return true;
  }

  memoized(parameter: ParameterDeclaration, context: Context, resolve: () => Values): Values {
    const key = this.index.key(parameter, context);
    if (this.pending.has(key)) return this.pending.get(key); // In progress means unknown.
    const cached = this.index.values.get(key);
    if (cached !== undefined) return cached;
    this.pending.set(key, undefined);
    const values = resolve();
    this.pending.set(key, values);
    return values;
  }

  finish(values: Values): readonly SuppliedValue[] | 'incomplete' {
    // Discard every partial result, including completed siblings. Returning []
    // here would let a caller fall back to a default initializer as proof.
    if (!this.complete) return 'incomplete';
    for (const [key, result] of this.pending) if (result !== undefined) this.index.values.set(key, result);
    return values ?? [];
  }
}
