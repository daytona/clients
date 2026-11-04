/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { BinaryExpression, ForInStatement, ForOfStatement, Project, SourceFile } from 'ts-morph';
import { unwrap } from './edits.js';

type BindingAssignment = BinaryExpression | ForInStatement | ForOfStatement;
type WriteIndex = ReadonlyMap<Node, readonly BindingAssignment[]>;
type Snapshot = { readonly tree: SourceFile['compilerNode']; readonly writes: WriteIndex };
const indexes = new WeakMap<Project, Map<SourceFile, Snapshot>>();

/** Index assignment targets once per source snapshot, not once per binding query. */
function writesIn(source: SourceFile): WriteIndex {
  const project = source.getProject();
  const sources = indexes.get(project) ?? new Map<SourceFile, Snapshot>();
  if (!indexes.has(project)) indexes.set(project, sources);
  const cached = sources.get(source);
  if (cached?.tree === source.compilerNode) return cached.writes;
  const writes = new Map<Node, BindingAssignment[]>();
  const targets = (target: Node): readonly Node[] => {
    const value = unwrap(target);
    if (Node.isIdentifier(value)) return value.getSymbol()?.getDeclarations() ?? [];
    if (Node.isShorthandPropertyAssignment(value)) return value.getValueSymbol()?.getDeclarations() ?? [];
    if (Node.isObjectLiteralExpression(value)) return value.getProperties().flatMap(targets);
    if (Node.isArrayLiteralExpression(value)) return value.getElements().flatMap(targets);
    if (Node.isPropertyAssignment(value)) return targets(value.getInitializerOrThrow());
    if (Node.isSpreadAssignment(value) || Node.isSpreadElement(value)) return targets(value.getExpression());
    return Node.isBinaryExpression(value) && value.getOperatorToken().getKind() === SyntaxKind.EqualsToken ? targets(value.getLeft()) : [];
  };
  const record = (target: Node, write: BindingAssignment): void => {
    for (const declaration of new Set(targets(target))) {
      const entries = writes.get(declaration) ?? [];
      entries.push(write);
      writes.set(declaration, entries);
    }
  };
  // Kind queries walk compiler nodes without materializing wrappers for every
  // token in dependency declarations. Preserve the original pre-order results.
  const candidates = [
    ...source.getDescendantsOfKind(SyntaxKind.BinaryExpression),
    ...source.getDescendantsOfKind(SyntaxKind.ForInStatement),
    ...source.getDescendantsOfKind(SyntaxKind.ForOfStatement),
  ].sort((left, right) => left.getStart() - right.getStart() || right.getEnd() - left.getEnd());
  for (const node of candidates) {
    if (Node.isBinaryExpression(node)) {
      const operator = node.getOperatorToken().getKind();
      if (operator >= SyntaxKind.FirstAssignment && operator <= SyntaxKind.LastAssignment) record(node.getLeft(), node);
    } else if (Node.isForOfStatement(node) || Node.isForInStatement(node)) record(node.getInitializer(), node);
  }
  sources.set(source, { tree: source.compilerNode, writes });
  return writes;
}

/** Only assignment targets count: property keys, defaults and member receivers are reads. */
export function bindingAssignments(binding: Node, source: SourceFile = binding.getSourceFile()): readonly BindingAssignment[] {
  return writesIn(source).get(binding) ?? [];
}
