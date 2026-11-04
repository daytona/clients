/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, realpathSync, statSync } from 'node:fs';
import { basename, dirname, resolve, sep } from 'node:path';
import type { SourceFile } from 'ts-morph';
import { relativePath, text } from './discovery.js';
import { envUrlItems, environmentLiteral } from './env-api-url.js';
import type { EnvironmentContext } from './env-api-url.js';
import { dotenvPreloads, DOTENV_LOAD_REVIEW } from './environment-loads.js';
import { location } from './edits.js';
import type { Item } from './model.js';
import type { ReadContext } from './read-failures.js';
import { composeEnvFiles } from './compose-env-files.js';
import { environmentText } from './environment-text.js';

type Reference = {
  readonly value: string | undefined; readonly line: number; readonly column: number;
  readonly bases?: ReadonlySet<string>; readonly directory?: boolean;
};

function literalPath(value: string | undefined): string | undefined {
  return value === undefined || /[*?\[\]`]|^~|\\(?:[^a-zA-Z0-9._-]|$)/.test(value) ? undefined : environmentLiteral(value);
}

function pathCandidates(path: string, bases: Iterable<string>): readonly string[] {
  // POSIX may also have a real filename containing backslashes. Scan both
  // interpretations rather than letting a clean file mask the other one.
  const paths = sep === '/' ? [path.replaceAll('\\', sep), path] : [path.replaceAll('\\', sep)];
  return [...new Set([...bases].flatMap(base => paths.map(value => resolve(base, value))))];
}

/** Resolve deployment/runtime file references independently of filename discovery. */
export function environmentFileReferences(files: readonly string[], sources: readonly SourceFile[], context: ReadContext & EnvironmentContext): {
  readonly files: ReadonlySet<string>; readonly composeFiles: ReadonlySet<string>; readonly items: readonly Item[];
} {
  const loaded = new Set<string>();
  const environments = new Set<string>();
  const composeFiles = new Set<string>();
  const composeDirectories = new Set<string>();
  const composeAncestors = new Map<string, ReadonlySet<string>>();
  const projectDirectories = new Set([context.root]);
  const selections: { readonly file: string; readonly entry: Reference }[] = [];
  const separators = new Set([':', ';']);
  const parsedCompose = new Map<string, ReturnType<typeof composeEnvFiles>>();
  const items: Item[] = [];
  const review = (position: Pick<Item, 'file' | 'line' | 'column'>): void => {
    items.push(...envUrlItems(position, { name: 'DAYTONA_API_URL', literal: undefined }, context));
  };
  const reference = (file: string, entry: Reference, target = loaded): void => {
    const position = { file: relativePath(context.root, file), line: entry.line, column: entry.column };
    const path = entry.value;
    if (!path || /\$|`/.test(path)) { review(position); return; }
    let found = false;
    for (const candidate of pathCandidates(path, entry.bases ?? [dirname(file)])) {
      if (!existsSync(candidate)) continue;
      const stat = context.failures?.read(candidate, () => statSync(candidate)) ?? (!context.failures ? statSync(candidate) : undefined);
      if (entry.directory ? stat?.isDirectory() : stat?.isFile()) {
        found = true;
        if (target === composeFiles) {
          const canonical = realpathSync(candidate);
          const ancestors = composeAncestors.get(file) ?? new Set<string>();
          // Keep lexical resolution, but bound each include chain by canonical identity.
          if (ancestors.has(canonical) || composeFiles.has(candidate)) continue;
          composeAncestors.set(candidate, new Set([...ancestors, canonical]));
          composeDirectories.add(dirname(candidate));
          composeDirectories.add(dirname(canonical));
        }
        target.add(candidate);
        if (entry.directory) target.add(realpathSync(candidate));
      }
      else review(position);
    }
    if (!found) review(position);
  };
  const control = (file: string, assignment: string, line = 0): void => {
    const match = assignment.match(/^\s*(?:export\s+)?(COMPOSE_FILE|COMPOSE_PATH_SEPARATOR)\s*=\s*([\s\S]*)$/);
    if (!match) return;
    const raw = match[2] ?? '';
    const value = /`|\\(?:[^a-zA-Z0-9._-]|$)/.test(raw) ? undefined : environmentLiteral(raw);
    const entry = { value, line, column: 0 };
    if (match[1] === 'COMPOSE_FILE') selections.push({ file, entry });
    else if (value) separators.add(value);
    else review({ file: relativePath(context.root, file), line, column: 0 });
  };
  // dotenv/config can override its default path through the environment or argv.
  // environmentLoads still scans .env; it cannot certify these implicit inputs.
  for (const source of sources) for (const preload of dotenvPreloads(source)) {
    items.push({ ...location(preload, context.root), kind: 'env-api-url', message: DOTENV_LOAD_REVIEW });
  }
  for (const file of files) {
    const name = basename(file);
    if (name !== 'package.json') continue;
    let manifest: unknown;
    try { manifest = JSON.parse(text(file, context.failures).replace(/^\uFEFF/, '') || '{}'); }
    catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      continue; // Manifest analysis owns malformed JSON diagnostics.
    }
    if (typeof manifest !== 'object' || manifest === null || !('scripts' in manifest) ||
      typeof manifest.scripts !== 'object' || manifest.scripts === null) continue;
    for (const script of Object.values(manifest.scripts)) {
      if (typeof script !== 'string') continue;
      // Preserve quoted word fragments so paths with spaces are one input.
      const words = [...script.matchAll(/&&|\|\||[;&|]|(?:[^\s'"\\;&|]|\\[\s\S]?|'[^']*'|"(?:\\[\s\S]|[^"\\])*")+/g)].map(match => match[0]);
      let compose = false;
      for (const [index, word] of words.entries()) {
        control(file, word);
        if (/^(?:&&|\|\||[;&|])$/.test(word)) compose = false;
        if (word === 'docker-compose' || word === 'docker' && words[index + 1] === 'compose') {
          compose = true;
          projectDirectories.add(dirname(file));
        }
        const composeFlag = compose ? word.match(/^(?:-f|--file)(?:=([\s\S]*))?$/) : undefined;
        if (composeFlag) {
          const value = composeFlag[1] ?? words[index + 1];
          selections.push({ file, entry: { value: literalPath(value), line: 0, column: 0 } });
        }
        const projectFlag = compose ? word.match(/^--project-directory(?:=([\s\S]*))?$/) : undefined;
        if (projectFlag) {
          reference(file, { value: literalPath(projectFlag[1] ?? words[index + 1]), line: 0, column: 0,
            directory: true }, projectDirectories);
        }
        const flag = word.match(/^--env-file(?:-if-exists)?(?:=([\s\S]*))?$/);
        if (flag) {
          const value = flag[1] ?? words[index + 1];
          const path = literalPath(value);
          reference(file, { value: path, line: 0, column: 0 }, compose && /^--env-file(?:=|$)/.test(word) ? environments : loaded);
        }
        const preload = /^(?:-r|--require)(?:=dotenv\/config)?$/.test(word) &&
          (word.endsWith('=dotenv/config') || /^(?:'dotenv\/config'|"dotenv\/config"|dotenv\/config)$/.test(words[index + 1] ?? ''));
        if (!preload) continue;
        items.push({ file: relativePath(context.root, file), line: 0, column: 0, kind: 'env-api-url', message: DOTENV_LOAD_REVIEW });
        const defaultFile = resolve(context.root, '.env');
        if (existsSync(defaultFile)) loaded.add(defaultFile);
      }
    }
  }
  for (const file of files.filter(file => /\.ya?ml$/i.test(file))) {
    const explicit = composeFiles.has(file) || /^(?:docker-compose|compose).*\.ya?ml$/.test(basename(file));
    if (basename(file) === '.gitlab-ci.yml' && !explicit) continue;
    const input = environmentText(file, context.failures);
    if (!input) continue;
    const parsed = composeEnvFiles(input.text.split(/\r?\n/), explicit, input.truncated);
    if (parsed.compose) {
      const canonical = realpathSync(file);
      composeFiles.add(file); parsedCompose.set(file, parsed);
      composeAncestors.set(file, new Set([canonical]));
      composeDirectories.add(dirname(file)); composeDirectories.add(dirname(canonical));
    }
  }
  const readEnvironments = new Set<string>();
  // Discover the full candidate set before resolving env_file: later manifests
  // and project-local .env files can supply additional bases and selections.
  let previousSize = -1;
  while (previousSize !== composeFiles.size + environments.size + projectDirectories.size) {
    previousSize = composeFiles.size + environments.size + projectDirectories.size;
    for (const file of environments) {
      if (readEnvironments.has(file)) continue;
      readEnvironments.add(file);
      const input = environmentText(file, context.failures);
      for (const [index, line] of (input?.text ?? '').split(/\r?\n/).entries()) control(file, line, index + 1);
    }
    const bases = new Set([...projectDirectories, ...composeDirectories]);
    for (const { file, entry } of selections) {
      if (!entry.value) { reference(file, entry, composeFiles); continue; }
      const groups = [entry.value.split(/[:;]/), ...[...separators].map(separator => entry.value?.split(separator) ?? [])];
      let resolved = false;
      for (const paths of groups) {
        let complete = true;
        for (const input of paths) {
          const path = literalPath(input);
          const candidates = path ? pathCandidates(path, [dirname(file), ...bases]).filter(existsSync) : [];
          complete &&= candidates.length > 0;
          for (const value of candidates) reference(file, { ...entry, value }, composeFiles);
        }
        resolved ||= complete;
      }
      if (!resolved) review({ file: relativePath(context.root, file), line: entry.line, column: entry.column });
    }
    for (const file of composeFiles) {
      if (!parsedCompose.has(file)) {
        const input = environmentText(file, context.failures);
        parsedCompose.set(file, composeEnvFiles((input?.text ?? '').split(/\r?\n/), true, input?.truncated));
      }
      bases.add(dirname(file));
      for (const include of parsedCompose.get(file)?.includes ?? []) {
        for (const entry of include.paths) reference(file, { ...entry, bases: new Set([dirname(file), ...bases]) }, composeFiles);
        if (include.projectDirectory) reference(file, { ...include.projectDirectory,
          bases: new Set([dirname(file), ...bases]), directory: true }, projectDirectories);
      }
    }
    for (const base of [...projectDirectories, ...composeDirectories]) bases.add(base);
    for (const base of bases) {
      const file = resolve(base, '.env');
      if (!existsSync(file)) continue;
      const stat = context.failures ? context.failures.read(file, () => statSync(file)) : statSync(file);
      if (!stat?.isFile()) continue;
      reference(file, { value: file, line: 0, column: 0 }, environments);
    }
    for (const file of environments) loaded.add(file);
  }
  const bases = new Set([...projectDirectories, ...composeDirectories]);
  for (const [file, parsed] of parsedCompose) {
    for (const entry of parsed.references) reference(file, { ...entry, bases });
    for (const include of parsed.includes) {
      const includeBases = new Set(bases);
      const directories = include.projectDirectory ? [include.projectDirectory] : include.paths;
      for (const entry of directories) {
        if (!entry.value || /\$|`/.test(entry.value)) continue;
        for (const candidate of pathCandidates(entry.value, [dirname(file), ...bases])) {
          includeBases.add(include.projectDirectory ? candidate : dirname(candidate));
        }
      }
      for (const entry of include.envFiles) reference(file, { ...entry, bases: includeBases });
    }
  }
  return { files: loaded, composeFiles, items };
}
