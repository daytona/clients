/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Track collection context, not YAML values; plain-scalar brackets are text. */
export function yamlContinuation(lines: readonly string[], start: number, indent: number): readonly [string | undefined, boolean] {
  const continuations: string[] = [];
  for (const following of lines.slice(start)) {
    if (!following.trim() || following.trimStart().startsWith('#')) continue;
    if (following.length - following.trimStart().length <= indent) break;
    continuations.push(withoutComment(following).trim());
  }
  return continuations.length === 1 ? [continuations[0], false] : [undefined, continuations.length > 1];
}

export function yamlBlockScalar(lines: readonly string[], lineNumber: number, line: string): readonly [boolean, string | undefined] {
  const marker = withoutComment(line).match(/:\s*((?:[&!]\S+\s+)*(?<style>[|>])(?<modifiers>(?:[1-9][+-]?|[+-][1-9]?)?))\s*$/);
  const style = marker?.groups?.['style'];
  const modifiers = marker?.groups?.['modifiers'];
  if (style === undefined || modifiers === undefined) return [false, undefined];
  const indent = line.length - line.trimStart().length;
  const explicit = modifiers.match(/[1-9]/)?.[0];
  let contentIndent = explicit === undefined ? undefined : indent + Number(explicit);
  const content: string[] = [];
  for (const following of lines.slice(lineNumber + 1)) {
    const followingIndent = following.length - following.trimStart().length;
    if (!following.trim()) {
      if (contentIndent !== undefined) content.push('');
      continue;
    }
    if (followingIndent <= indent) break;
    contentIndent ??= followingIndent;
    if (followingIndent < contentIndent) break;
    content.push(following.slice(contentIndent));
  }
  let scalar = (style === '|' ? content.join('\n') : content.join(' ')).trim();
  if (!/^[A-Za-z][A-Za-z0-9+.-]*:\/\/\S+$/.test(scalar)) return [true, undefined];
  if (modifiers.includes('+')) scalar += '\n';
  else if (!modifiers.includes('-')) scalar += '\n';
  return [true, scalar];
}

export function yamlBlockScalarBodies(lines: readonly string[]): readonly boolean[] {
  const bodies = lines.map(() => false);
  let parentIndent: number | undefined;
  for (const [index, line] of lines.entries()) {
    const indent = line.length - line.trimStart().length;
    if (parentIndent !== undefined) {
      if (!line.trim() || indent > parentIndent) {
        bodies[index] = true;
        continue;
      }
      parentIndent = undefined;
    }
    if (/:\s*(?:[&!]\S+\s+)*(?:[|>](?:[1-9][+-]?|[+-][1-9]?)?)\s*$/.test(withoutComment(line))) parentIndent = indent;
  }
  return bodies;
}

function withoutComment(line: string): string {
  const comment = [...line.matchAll(/"(?:\\.|[^"\\])*"|'[^']*'|(?<comment>(?<!\S)#)/g)]
    .find(token => token.groups?.['comment']);
  return line.slice(0, comment?.index ?? line.length);
}

export class YamlFlow {
  private readonly closers: string[] = [];
  private quote = '';
  private blockIndent: number | undefined;
  private pendingIndent: number | undefined;

  line(text: string): readonly boolean[] {
    const indent = text.length - text.trimStart().length;
    if (!text.trim() || this.blockIndent !== undefined && indent > this.blockIndent) return Array.from(text, () => false);
    this.blockIndent = undefined;
    let scalarIndent = this.pendingIndent !== undefined && indent > this.pendingIndent ? this.pendingIndent : indent;
    this.pendingIndent = undefined;
    const flow: boolean[] = [];
    let ready = true;
    let plain = false;
    for (let index = 0; index < text.length; index++) {
      const character = text[index] ?? '';
      const next = text[index + 1] ?? '';
      flow[index] = this.closers.length > 0;
      if (this.quote) {
        if (this.quote === '"' && character === '\\' || this.quote === "'" && character === "'" && next === "'") {
          flow[++index] = this.closers.length > 0;
        } else if (character === this.quote) this.quote = '';
        continue;
      }
      if (/\s/.test(character)) continue;
      if (character === ':' && (next === '' || /\s/.test(next) || this.closers.length > 0 && /[\[{'"]/.test(next))) {
        ready = true;
        plain = false;
        if (this.closers.length === 0) scalarIndent = text.match(/^\s*(?:-\s+)*/)?.[0].length ?? indent;
      } else if (this.closers.length > 0 && character === ',') {
        ready = true;
      } else if (character === this.closers.at(-1)) {
        this.closers.pop();
        ready = false;
        plain = false;
      } else if (ready && (character === '[' || character === '{')) {
        this.closers.push(character === '[' ? ']' : '}');
      } else if (ready && (character === '"' || character === "'")) {
        this.quote = character;
        ready = false;
      } else if (ready && character === '-' && /\s/.test(next) && this.closers.length === 0) {
        scalarIndent = index;
        continue;
      } else if (ready && (character === '&' || character === '!')) {
        // Node properties precede the actual value, including a flow collection.
        const property = text.slice(index).match(/^[&!]\S+\s+/)?.[0];
        if (property) {
          for (let end = index + property.length; index < end - 1; index++) flow[index + 1] = this.closers.length > 0;
        } else ready = false;
      } else {
        ready = false;
        plain = this.closers.length === 0;
      }
    }
    if (this.closers.length === 0) {
      this.quote = '';
      if (plain) this.blockIndent = scalarIndent;
      if (ready) this.pendingIndent = scalarIndent;
    }
    return flow;
  }
}
