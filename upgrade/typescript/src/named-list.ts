/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, ts } from 'ts-morph';
import type { Edit } from './model.js';
import { applyEdits } from './edits.js';

/** Keep each specifier's trivia when a brace list is partitioned between modules. */
export function namedList(brace: Node, selected: readonly Node[], style: string | {
  readonly newline: string; readonly edits: readonly Edit[];
}): string {
  const text = brace.getSourceFile().getFullText();
  const newline = typeof style === 'string' ? style : style.newline;
  const render = (start: number, end: number): string => applyEdits(text.slice(start, end), typeof style === 'string' ? [] :
    style.edits.filter(edit => edit.start >= start && edit.end <= end).map(edit => ({ ...edit, start: edit.start - start, end: edit.end - start })));
  const original = brace.getText();
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, ts.LanguageVariant.Standard, original);
  let commented = false;
  for (let token = scanner.scan(); token !== SyntaxKind.EndOfFileToken; token = scanner.scan()) {
    if (token === SyntaxKind.SingleLineCommentTrivia || token === SyntaxKind.MultiLineCommentTrivia) {
      commented = true;
      break;
    }
  }
  if (!/[\r\n\u2028\u2029]/u.test(original) && !commented) {
    return `{ ${selected.map(entry => render(entry.getStart(), entry.getEnd())).join(', ')} }`;
  }
  const entries = brace.getChildren().flatMap(child => child.getKind() === SyntaxKind.SyntaxList ? child.getChildren() : [])
    .filter(child => Node.isImportSpecifier(child) || Node.isExportSpecifier(child) || Node.isBindingElement(child));
  const closing = brace.getLastChildByKindOrThrow(SyntaxKind.CloseBraceToken);
  const closingIndent = text.slice(text.lastIndexOf('\n', closing.getStart() - 1) + 1, closing.getStart()).match(/^[\t ]*/u)?.[0] ?? '';
  const indent = original.match(/(?:\r\n|[\r\n\u2028\u2029])([\t ]*)\S/u)?.[1] ?? `${closingIndent}  `;
  const chunks: string[] = [];
  let start = brace.getStart() + 1;
  for (const [index, entry] of entries.entries()) {
    const next = entries[index + 1];
    const comma = entry.getNextSiblingIfKind(SyntaxKind.CommaToken);
    let end = comma?.getEnd() ?? entry.getEnd();
    if (next) {
      for (const comment of comma?.getTrailingCommentRanges() ?? []) {
        // Next-line directives lead the following entry, even after a comma.
        if (/^\/\/\s*(?:@ts-(?:ignore|expect-error)|eslint-disable-next-line|biome-ignore|prettier-ignore)(?:\s|$)/u.test(comment.getText())) break;
        // An inline block immediately before the next name leads that name.
        if (comment.getKind() === SyntaxKind.MultiLineCommentTrivia &&
            !/[\r\n\u2028\u2029]/u.test(text.slice(comment.getEnd(), next.getStart()))) break;
        end = comment.getEnd();
      }
    } else end = closing.getStart(); // Closing-list comments travel with the final name.
    if (selected.includes(entry)) chunks.push(`${indent}${render(start, end).trim()}`);
    start = end;
  }
  return `{${newline}${chunks.join(newline)}${newline}${closingIndent}}`;
}
