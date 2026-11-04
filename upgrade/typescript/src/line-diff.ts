/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

export type LineEdit = { readonly prefix: ' ' | '-' | '+'; readonly text: string };
type Line = { readonly text: string; readonly index: number };
type Span = { readonly lines: readonly Line[]; readonly start: number; readonly end: number };
type Match = { readonly old: number; readonly next: number };

// Hirschberg needs only one score row in each direction, not an n*m matrix.
function scores(old: Span, next: Span, reverse: boolean): Uint32Array {
  const width = next.end - next.start;
  const row = new Uint32Array(width + 1);
  for (let offset = 0; offset < old.end - old.start; offset++) {
    const line = old.lines[reverse ? old.end - offset - 1 : old.start + offset];
    let diagonal = 0;
    for (let column = 1; column <= width; column++) {
      const previous = row[column] ?? 0;
      const other = next.lines[reverse ? next.end - column : next.start + column - 1];
      row[column] = line?.text === other?.text ? diagonal + 1 : Math.max(previous, row[column - 1] ?? 0);
      diagonal = previous;
    }
  }
  return row;
}

// Keep score-row lifetimes outside recursion so auxiliary space stays linear.
function split(old: Span, next: Span): number {
  const middle = Math.floor((old.start + old.end) / 2);
  const left = scores({ ...old, end: middle }, next, false);
  const right = scores({ ...old, start: middle }, next, true);
  let best = -1;
  let column = 0;
  for (let index = 0; index <= next.end - next.start; index++) {
    const score = (left[index] ?? 0) + (right[next.end - next.start - index] ?? 0);
    if (score > best) { best = score; column = index; }
  }
  return next.start + column;
}

export function lineEdits(before: string, after: string): readonly LineEdit[] {
  // Preserve terminators: a final-newline change must remain a real edit.
  const oldLines = before.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const newLines = after.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const oldSet = new Set(oldLines);
  const newSet = new Set(newLines);
  // Lines absent from the other file cannot participate in any LCS. Filtering
  // them lets large, mostly-unchanged files align without quadratic work.
  const commonOld = oldLines.map((text, index) => ({ text, index })).filter(line => newSet.has(line.text));
  const commonNew = newLines.map((text, index) => ({ text, index })).filter(line => oldSet.has(line.text));
  const matches: Match[] = [];
  const align = (old: Span, next: Span): void => {
    let startOld = old.start; let startNew = next.start;
    while (startOld < old.end && startNew < next.end) {
      const left = old.lines[startOld]; const right = next.lines[startNew];
      if (!left || !right || left.text !== right.text) break;
      matches.push({ old: left.index, next: right.index }); startOld++; startNew++;
    }
    let tail = 0;
    while (tail < Math.min(old.end - startOld, next.end - startNew) &&
      old.lines[old.end - tail - 1]?.text === next.lines[next.end - tail - 1]?.text) tail++;
    const middleOld = { ...old, start: startOld, end: old.end - tail };
    const middleNew = { ...next, start: startNew, end: next.end - tail };
    if (middleOld.end - startOld === 1) {
      const left = old.lines[startOld];
      for (let index = startNew; left && index < middleNew.end; index++) {
        const right = next.lines[index];
        if (right?.text === left.text) { matches.push({ old: left.index, next: right.index }); break; }
      }
    } else if (startOld < middleOld.end && startNew < middleNew.end) {
      const middle = Math.floor((startOld + middleOld.end) / 2);
      const column = split(middleOld, middleNew);
      align({ ...middleOld, end: middle }, { ...middleNew, end: column });
      align({ ...middleOld, start: middle }, { ...middleNew, start: column });
    }
    for (let offset = tail; offset > 0; offset--) {
      const left = old.lines[old.end - offset]; const right = next.lines[next.end - offset];
      if (left && right) matches.push({ old: left.index, next: right.index });
    }
  };
  align({ lines: commonOld, start: 0, end: commonOld.length }, { lines: commonNew, start: 0, end: commonNew.length });
  const edits: LineEdit[] = [];
  let old = 0; let next = 0;
  for (const match of [...matches, { old: oldLines.length, next: newLines.length }]) {
    for (const text of oldLines.slice(old, match.old)) edits.push({ prefix: '-', text });
    for (const text of newLines.slice(next, match.next)) edits.push({ prefix: '+', text });
    const text = oldLines[match.old];
    if (text !== undefined) edits.push({ prefix: ' ', text });
    old = match.old + 1; next = match.next + 1;
  }
  return edits;
}
