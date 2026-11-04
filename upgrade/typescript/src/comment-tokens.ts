/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Edit } from './model.js';

export function suggestionCommentEdits(source: SourceFile): readonly Edit[] {
  const text = source.getFullText();
  const nodes = [source, ...source.getDescendants()];
  const protectedRanges = nodes.filter(node => Node.isStringLiteral(node) || Node.isNoSubstitutionTemplateLiteral(node) ||
    Node.isTemplateHead(node) || Node.isTemplateMiddle(node) || Node.isTemplateTail(node) ||
    Node.isJsxText(node) || Node.isRegularExpressionLiteral(node));
  const edits = new Map<number, Edit>();
  for (const node of nodes) for (const range of [...node.getLeadingCommentRanges(), ...node.getTrailingCommentRanges()]) {
    if (range.getKind() !== SyntaxKind.SingleLineCommentTrivia || !range.getText().startsWith('// daytona-v2:')) continue;
    const start = range.getPos();
    if (protectedRanges.some(protectedNode => start >= (Node.isJsxText(protectedNode) ? protectedNode.getPos() : protectedNode.getStart()) && start < protectedNode.getEnd())) continue;
    let lineStart = start;
    while (lineStart > 0 && !/[\r\n\u2028\u2029]/u.test(text.charAt(lineStart - 1))) lineStart--;
    if (!/^\s*$/u.test(text.slice(lineStart, start))) continue;
    const terminator = /\r\n|[\r\n\u2028\u2029]/u.exec(text.slice(range.getEnd()));
    const lineEnd = terminator ? range.getEnd() + terminator.index + terminator[0].length : text.length;
    edits.set(lineStart, { start: lineStart, end: lineEnd, text: '' });
  }
  return [...edits.values()];
}
