/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, lstatSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { validRange } from 'semver';
import type { Item } from './model.js';
import { relativePath } from './discovery.js';
import { aboveTargetMajor, atOrAboveTarget, pinnedFloor } from './sdk-target.js';

type RuntimeDependency = { readonly name: '@daytonaio/sdk' | '@daytona/sdk'; readonly range: string | null };

function runtimeDependencies(source: string): readonly RuntimeDependency[] {
  // Match manifest parsing elsewhere: syntax errors are not dependency proof.
  const manifest: unknown = JSON.parse(source.replace(/^\uFEFF/, ''));
  if (typeof manifest !== 'object' || manifest === null) return [];
  return Object.entries(manifest).flatMap(([section, entries]: readonly [string, unknown]) => {
    if (!['dependencies', 'devDependencies', 'optionalDependencies'].includes(section) ||
        typeof entries !== 'object' || entries === null) return [];
    return Object.entries(entries).flatMap(([name, range]: readonly [string, unknown]) =>
      name === '@daytonaio/sdk' || name === '@daytona/sdk' ? [{ name, range: typeof range === 'string' ? range : null }] : []);
  });
}

export function dependencyScopeItem(root: string, rewritten: string, dependencyDirectories: ReadonlySet<string>, target: string): Item | undefined {
  const normalize = (path: string): string => resolve(path).replaceAll('\\', '/');
  const base = normalize(root);
  const rewrittenPath = normalize(rewritten);
  const dependencies = new Set([...dependencyDirectories].map(normalize));
  let boundary = base;
  for (let directory = base; ; directory = dirname(directory)) {
    if (existsSync(join(directory, '.git'))) { boundary = directory; break; }
    if (dirname(directory) === directory) break;
  }
  for (let directory = dirname(rewrittenPath); ; directory = dirname(directory)) {
    if (dependencies.has(directory)) return undefined;
    const file = join(directory, 'package.json');
    // Only a regular file is a manifest; a directory named package.json is skipped.
    if (existsSync(file) && lstatSync(file).isFile()) {
      let declarations: readonly RuntimeDependency[];
      try { declarations = runtimeDependencies(readFileSync(file, 'utf8')); }
      catch (error) {
        if (!(error instanceof SyntaxError) && !(error instanceof Error && 'code' in error)) throw error;
        // A project manifest that cannot be read is already an unsupported-file
        // item that blocks the run; only manifests above the root report here.
        if (!(error instanceof SyntaxError) && (normalize(file) + '/').startsWith(base + '/')) return undefined;
        return { file: relativePath(base, file), line: 0, column: 0, kind: 'manual-dependency',
          message: `Cannot read ancestor manifest ${relativePath(base, file)}; verify "@daytona/sdk": "${target}" where the project declares its dependencies.` };
      }
      if (declarations.length) {
        const certified = declarations.every(({ name, range }) => {
          if (name !== '@daytona/sdk' || range === null || validRange(range) === null) return false;
          // Only the target or a later release of its major certifies: a stable
          // floor under a pre-release target (`^2.0.0`) installs nothing yet.
          return atOrAboveTarget(range, target);
        });
        if (certified) return undefined;
        // The same decisions as in the project root: a later major is never
        // downgraded, a pre-release range above the target keeps its own floor.
        const own = declarations.find(({ range }) => range !== null && aboveTargetMajor(range, target));
        if (own !== undefined) return { file: relativePath(base, file), line: 0, column: 0, kind: 'manual-dependency',
          message: `${own.name}@${own.range ?? ''} is newer than this daytona-upgrade release targets (${target}); use the daytona-upgrade release of that SDK version.` };
        const range = declarations.find(({ name }) => name === '@daytona/sdk')?.range;
        const wanted = (range === null || range === undefined ? undefined : pinnedFloor(range, target)) ?? target;
        return { file: relativePath(base, file), line: 0, column: 0, kind: 'manual-dependency',
          message: `no Daytona dependency was updated; change the dependency in this file to "@daytona/sdk": "${wanted}"` };
      }
    }
    if (directory === boundary) break;
  }
  return { file: relativePath(base, rewritten), line: 0, column: 0, kind: 'manual-dependency',
    message: `no Daytona dependency was updated; add "@daytona/sdk": "${target}" where the project declares its dependencies` };
}
