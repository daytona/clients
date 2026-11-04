/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { realpathSync } from 'node:fs';
import { dirname, relative, resolve, win32 } from 'node:path';
import { ts } from 'ts-morph';
import type { Project, SourceFile } from 'ts-morph';
import type { Item } from './model.js';
import { matchesPathAlias } from './project-modules.js';

export type ProjectGroup = {
  readonly project: Project;
  readonly sources: readonly SourceFile[];
  readonly included: ReadonlySet<string>;
};

function strings(raw: unknown, key: string): readonly string[] | undefined {
  if (typeof raw !== 'object' || raw === null || !(key in raw)) return undefined;
  const value: unknown = Reflect.get(raw, key);
  return Array.isArray(value) ? value.filter((entry: unknown): entry is string => typeof entry === 'string') : undefined;
}

/** Prefer alias-owning contexts, then explicit files, include, distance, filename. */
export function configRanks(config: string, parsed: ts.ParsedCommandLine): ReadonlyMap<string, readonly number[]> {
  const directory = dirname(config);
  const explicit = strings(parsed.raw, 'files');
  const includes = strings(parsed.raw, 'include') ?? (explicit ? [] : ['**/*']);
  const patterns = includes.map(pattern => {
    const absolute = resolve(directory, pattern).replaceAll('\\', '/');
    return { files: new Set(ts.sys.readDirectory(directory, undefined, strings(parsed.raw, 'exclude'), [pattern])),
      rank: [absolute.search(/[?*]/) < 0 ? absolute.length : absolute.search(/[?*]/), -(pattern.match(/[?*]/g)?.length ?? 0)] };
  }).sort((left, right) => compareRank(right.rank, left.rank));
  const filename = win32.basename(config);
  return new Map(parsed.fileNames.map(file => {
    const preferred = /\.(?:spec|test)\.[^.]+$/.test(file) ? /^(?:ts|js)config\.spec\.json$/ : /^(?:ts|js)config\.app\.json$/;
    const aliases = Object.keys(parsed.options.paths ?? {});
    const mapped = aliases.length ? ts.preProcessFile(ts.sys.readFile(file) ?? '', true, true).importedFiles
      .filter(imported => matchesPathAlias(imported.fileName, aliases)).length : 0;
    return [file, [mapped, Number(explicit?.some(entry => resolve(directory, entry).replaceAll('\\', '/') === file) ?? false),
      ...(patterns.find(pattern => pattern.files.has(file))?.rank ?? [0, 0]), directory.replaceAll('\\', '/').split('/').length,
      Number(preferred.test(filename)), Number(!/^(?:ts|js)config\.json$/.test(filename))]];
  }));
}

export function compareRank(left: readonly number[], right: readonly number[]): number {
  for (let index = 0; index < Math.max(left.length, right.length); index++) {
    const difference = (left[index] ?? 0) - (right[index] ?? 0);
    if (difference) return difference;
  }
  return 0;
}

/** Compare actual import targets, not paths text: equivalent aliases are safe. */
export function configConflictItems(groups: readonly ProjectGroup[], root: string): readonly Item[] {
  return groups.flatMap(group => group.sources.flatMap(source => {
    const path = source.getFilePath();
    const candidates = groups.filter(candidate => candidate.included.has(path));
    if (candidates.length < 2) return [];
    const imports = ts.preProcessFile(source.getFullText(), true, true).importedFiles;
    const conflict = imports.some(imported => {
      if (/^\.{1,2}\//.test(imported.fileName)) return false;
      const contexts = candidates.map(candidate => candidate.project.getSourceFile(path)).filter((value): value is SourceFile =>
        value !== undefined && matchesPathAlias(imported.fileName, Object.keys(value.getProject().getCompilerOptions().paths ?? {})));
      const targets = contexts.map(context => ts.resolveModuleName(imported.fileName, path,
        context.getProject().getCompilerOptions(), context.getProject().getModuleResolutionHost()).resolvedModule?.resolvedFileName);
      return new Set(targets.map(target => {
        if (target === undefined) return undefined;
        try { return realpathSync(target); }
        catch (error) {
          if (!(error instanceof Error) || !('code' in error)) throw error;
          // A disappearing or inaccessible target remains a distinct, conservative conflict candidate.
          return target;
        }
      })).size > 1;
    });
    const file = relative(root, path).replaceAll('\\', '/');
    return conflict ? [{ file, line: 0, column: 0, kind: 'fixme' as const,
      message: `${file} is included by several tsconfig files that resolve its imports differently; review it manually.` }] : [];
  }));
}
