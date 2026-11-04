/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ts } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { Item } from './model.js';
import { sourceLocation } from './edits.js';
import { isProjectLocalModule } from './project-modules.js';

export function sourceSupport(source: SourceFile, root: string, workspaceNames: readonly string[] = []): {
  readonly supported: boolean; readonly blocking: boolean; readonly items: readonly Item[];
} {
  const diagnostics = source.getProject().getProgram().getSyntacticDiagnostics(source);
  if (!diagnostics.length) return { supported: true, blocking: false, items: [] };
  // Skip comment trivia without stripping comment-like text inside strings.
  // Token matching also covers partially parsed imports/requires.
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, true, ts.LanguageVariant.Standard, source.getFullText());
  const tokens: string[] = [];
  while (scanner.scan() !== ts.SyntaxKind.EndOfFileToken) tokens.push(scanner.getTokenText());
  const imports = [...tokens.join(' ').matchAll(/(?:\b(?:import|export)\s[\s\S]*?\bfrom\s*|\bimport\s*|\b(?:require|import)\s*\(\s*)['"]([^'"]+)['"]/g)];
  const related = /daytona/i.test(source.getFullText()) || imports.some(match => isProjectLocalModule(source, match[1] ?? '', workspaceNames));
  if (!related) return { supported: false, blocking: false, items: [{ ...sourceLocation(source, 0, root), kind: 'unsupported-file',
    message: 'Skipped unparsable source without Daytona mentions or local imports; no edits were made.' }] };
  return { supported: false, blocking: true, items: diagnostics.map(diagnostic => ({
    ...sourceLocation(source, diagnostic.getStart(), root), kind: 'unsupported-file',
    message: `Cannot parse TypeScript/JavaScript source: ${ts.flattenDiagnosticMessageText(diagnostic.compilerObject.messageText, ' ')}`,
  })) };
}
