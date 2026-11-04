/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, ts } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Edit, Item, ItemKind } from './model.js';
import { UpgradeError } from './model.js';
import { relative } from 'node:path';
import { suggestionCommentEdits } from './comment-tokens.js';

const originalTexts = new WeakMap<SourceFile, string>();
export function registerOriginal(source: SourceFile, text: string): void { originalTexts.set(source, text); }

export function applyEdits(text: string, edits: readonly Edit[]): string {
  let result = text;
  let boundary = text.length;
  for (const edit of [...edits].sort((a, b) => b.start - a.start || b.end - a.end)) {
    if (edit.end > boundary) throw new UpgradeError(`Overlapping edits at ${edit.start}`);
    result = result.slice(0, edit.start) + edit.text + result.slice(edit.end);
    boundary = edit.start;
  }
  return result;
}
export function replace(node: Node, text: string): Edit {
  return { start: node.getStart(), end: node.getEnd(), text };
}
export function location(node: Node, root: string): Pick<Item, 'file' | 'line' | 'column'> {
  return sourceLocation(node.getSourceFile(), node.getStart(), root);
}
export function sourceLocation(source: SourceFile, offset: number, root: string): Pick<Item, 'file' | 'line' | 'column'> {
  const position = source.getLineAndColumnAtPos(offset);
  const bom = originalTexts.get(source)?.startsWith('\uFEFF') && !source.getFullText().startsWith('\uFEFF');
  return { file: relative(root, source.getFilePath()).replaceAll('\\', '/'),
    line: position.line, column: position.column + (bom && position.line === 1 ? 1 : 0) };
}
export function item(node: Node, detail: { readonly kind: ItemKind; readonly message: string }, root: string): Item {
  return { ...location(node, root), ...detail };
}
export function statement(node: Node): Node {
  return node.getAncestors().find(parent => Node.isStatement(parent) && !Node.isBlock(parent)) ?? node;
}
export function commentEdit(node: Node, lines: readonly string[]): Edit {
  const source = node.getSourceFile().getFullText();
  const start = node.getStart();
  let lineStart = start;
  while (lineStart > 0 && !/[\r\n\u2028\u2029]/u.test(source.charAt(lineStart - 1))) lineStart--;
  const prefix = source.slice(lineStart, start);
  const indentation = /^\s*$/.test(prefix) ? prefix : prefix.match(/^\s*/)?.[0] ?? '';
  const newline = source.match(/\r\n|[\r\n\u2028\u2029]/u)?.[0] ?? '\n';
  // Inline statements need a line break, otherwise the comment would consume code.
  const inline = !/^\s*$/.test(prefix);
  const whitespace = prefix.match(/[\t ]*$/u)?.[0] ?? '';
  const comments = lines.map(line => {
    const tag = line.startsWith('daytona-v2:') ? 'daytona-v2: ' : '';
    const continued = line.replace(/\r\n|[\r\n\u2028\u2029]/gu, terminator => `${terminator}${indentation}// ${tag}`);
    return `${indentation}// ${continued}${newline}`;
  }).join('');
  const insertionStart = inline ? start - whitespace.length : lineStart;
  return { start: insertionStart, end: inline ? start : lineStart,
    text: `${inline ? newline : ''}${comments}${inline ? indentation : ''}` };
}
export function displayText(text: string): string {
  const source = ts.createSourceFile('display.ts', text, ts.ScriptTarget.Latest, true);
  const ranges: { readonly start: number; readonly end: number }[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateExpression(node) || ts.isRegularExpressionLiteral(node)) {
      ranges.push({ start: node.getStart(source), end: node.getEnd() });
    } else ts.forEachChild(node, visit);
  };
  visit(source);
  let offset = 0;
  let result = '';
  for (const range of ranges) {
    result += text.slice(offset, range.start).replace(/\s+/gu, ' ') + text.slice(range.start, range.end);
    offset = range.end;
  }
  return (result + text.slice(offset).replace(/\s+/gu, ' ')).trim();
}
export function removeSuggestionLines(source: SourceFile): string {
  return applyEdits(source.getFullText(), suggestionCommentEdits(source));
}
export function unwrap(node: Node, preserveAwait = false): Node {
  if (Node.isAwaitExpression(node) && !preserveAwait || Node.isParenthesizedExpression(node) || Node.isAsExpression(node) ||
      Node.isTypeAssertion(node) || Node.isNonNullExpression(node) || Node.isSatisfiesExpression(node)) return unwrap(node.getExpression(), preserveAwait);
  return node;
}
export function initializer(node: Node): Node | undefined {
  // A parameter default is conditional: callers may supply an unrelated value.
  // An explicit annotation is independent evidence, handled by annotatedKind.
  if (Node.isVariableDeclaration(node) || Node.isPropertyDeclaration(node) || Node.isPropertyAssignment(node)) return node.getInitializer();
  if (Node.isBinaryExpression(node) && node.getOperatorToken().getKind() === SyntaxKind.EqualsToken) return node.getRight();
  return undefined;
}
export function propertyRemoval(node: Node): Edit {
  const comma = node.getNextSiblingIfKind(SyntaxKind.CommaToken);
  if (comma) return { start: node.getStart(), end: comma.getEnd(), text: '' };
  const previous = node.getPreviousSiblingIfKind(SyntaxKind.CommaToken);
  return { start: previous?.getStart() ?? node.getStart(), end: node.getEnd(), text: '' };
}
export function cleanRemoval(node: Node, edit: Edit): Edit {
  const text = node.getSourceFile().getFullText();
  const lineStart = text.lastIndexOf('\n', edit.start - 1) + 1;
  const lineEnd = text.indexOf('\n', edit.end);
  if (/^[\t ]*$/.test(text.slice(lineStart, edit.start)) &&
    /^[\t \r]*$/.test(text.slice(edit.end, lineEnd < 0 ? text.length : lineEnd))) {
    return { ...edit, start: lineStart, end: lineEnd < 0 ? text.length : lineEnd + 1 };
  }
  return edit;
}
