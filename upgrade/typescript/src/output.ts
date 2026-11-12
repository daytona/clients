/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Change, Report } from './model.js';
import { relativePath } from './discovery.js';
import { lineEdits } from './line-diff.js';
export function humanReport(report: Report): string {
  const lines = [report.refused ? `Refused: ${report.refused.message}` : `${report.dryRun ? 'Would change' : 'Changed'} ${report.changedFiles.length} files.`];
  for (const file of report.changedFiles) lines.push(`  ${file}`);
  for (const dependency of report.dependencies) lines.push(`  ${dependency.file}: ${dependency.from} → ${dependency.to}`);
  for (const kind of [...new Set(report.items.map(item => item.kind))]) {
    lines.push(`${kind}:`);
    for (const item of report.items.filter(item => item.kind === kind)) lines.push(`  ${item.file}:${item.line}:${item.column}: ${item.message}`);
  }
  if (report.warnings?.length) lines.push('Warnings:', ...report.warnings.map(warning => `  ${warning.file}: ${warning.message}`));
  if (report.nextSteps.length) lines.push('Next steps:', ...report.nextSteps.map(step => `  ${step}`));
  return lines.join('\n') + '\n';
}
export function diff(changes: readonly Change[], root?: string): string {
  return changes.map(change => {
    if (change.before === change.after) return '';
    const edits = lineEdits(change.before, change.after);
    // Mutable ranges accumulate overlapping three-line context windows.
    const hunks: { readonly start: number; end: number }[] = [];
    for (const [index, edit] of edits.entries()) {
      if (edit.prefix === ' ') continue;
      const start = Math.max(0, index - 3); const end = Math.min(edits.length, index + 4);
      const previous = hunks.at(-1);
      if (previous && start <= previous.end) previous.end = end;
      else hunks.push({ start, end });
    }
    let oldLine = 1; let newLine = 1; let cursor = 0;
    const file = root === undefined ? change.file : relativePath(root, change.file);
    const output = [root === undefined ? `--- ${file}\n+++ ${file}\n` : `--- a/${file}\n+++ b/${file}\n`];
    for (const hunk of hunks) {
      while (cursor < hunk.start) {
        const edit = edits[cursor++];
        if (edit?.prefix !== '+') oldLine++;
        if (edit?.prefix !== '-') newLine++;
      }
      const body = edits.slice(hunk.start, hunk.end);
      const oldCount = body.filter(edit => edit.prefix !== '+').length;
      const newCount = body.filter(edit => edit.prefix !== '-').length;
      output.push(`@@ -${oldCount ? oldLine : oldLine - 1},${oldCount} +${newCount ? newLine : newLine - 1},${newCount} @@\n`);
      for (const edit of body) {
        output.push(`${edit.prefix}${edit.text}`);
        if (!edit.text.endsWith('\n')) output.push('\n\\ No newline at end of file\n');
      }
      oldLine += oldCount; newLine += newCount; cursor = hunk.end;
    }
    return output.join('');
  }).join('');
}
