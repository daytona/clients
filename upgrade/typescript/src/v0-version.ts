/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, lstatSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { compare, minVersion, parse, validRange } from 'semver';
import type { SemVer } from 'semver';
import type { Warning } from './model.js';
import { relativePath } from './discovery.js';

/** A Daytona 0.x dependency a manifest declares, with its literal range. */
export type DeclaredDependency = { readonly file: string; readonly range: string };

export type V0VersionCheck = { readonly warnings: readonly Warning[]; readonly refusal?: string };

const NAMES = ['@daytonaio/sdk', '@daytona/sdk'] as const;
// Closest first, like the package managers: the nearest directory with a lockfile owns the install.
const LOCKFILES = ['package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lock'] as const;
const NAME = String.raw`(@daytonaio\/sdk|@daytona\/sdk)`;
const VERSION = String.raw`(\d+\.\d+\.\d+[^\s"':,()]*)`;

export const olderMessage = (version: string, pinned: string): string =>
  `Daytona SDK ${version} is older than ${pinned}, the 0.x release SDK 2.x runs as .v0. Calls moved to .v0 now run ${pinned}; review the 0.x changes between ${version} and ${pinned}, then run your tests.`;
export const newerPatchMessage = (version: string, pinned: string): string =>
  `Daytona SDK ${version} is newer than ${pinned}, the 0.x release SDK 2.x runs as .v0. Calls moved to .v0 now run ${pinned}; review the 0.x changes between ${pinned} and ${version}, then run your tests.`;
export const allowedNewerMessage = (version: string, pinned: string): string =>
  `Daytona SDK ${version} is newer than ${pinned}, the 0.x release SDK 2.x runs as .v0. 0.x APIs added after ${pinned} are missing from .v0 and get no FIXME (--allow-newer-v0); review them by hand.`;
export const unknownMessage = (pinned: string): string =>
  `Cannot determine which Daytona SDK 0.x version this project uses; SDK 2.x runs ${pinned} as .v0. Review the 0.x changes between your version and ${pinned}, then run your tests.`;
export const newerRefusal = (version: string, file: string, pinned: string): string =>
  `Daytona SDK ${version} (${file}) is newer than ${pinned}, the 0.x release this daytona-upgrade and SDK 2.x run as .v0: 0.x APIs added after ${pinned} are missing from .v0 and would get no FIXME. Use a later daytona-upgrade release, or rerun with --allow-newer-v0 to migrate anyway. No files were written.`;

function zeroX(value: string): SemVer | undefined {
  const version = parse(value);
  return version !== null && version.major === 0 ? version : undefined;
}

function lockedVersions(file: string): readonly string[] {
  const text = readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  const name = file.slice(dirname(file).length + 1);
  if (name === 'package-lock.json' || name === 'npm-shrinkwrap.json') {
    const lock: unknown = JSON.parse(text);
    if (typeof lock !== 'object' || lock === null) return [];
    const entries = [
      // lockfileVersion 2/3: the root's and each workspace's own copy, not
      // copies nested under another package (those belong to that package).
      ...Object.entries('packages' in lock && typeof lock.packages === 'object' && lock.packages !== null ? lock.packages : {})
        .filter(([path]) => NAMES.some(packageName => path === `node_modules/${packageName}` ||
          path.endsWith(`/node_modules/${packageName}`) && !path.slice(0, -`/node_modules/${packageName}`.length).includes('node_modules/'))),
      // lockfileVersion 1: the top-level dependency tree.
      ...Object.entries('dependencies' in lock && typeof lock.dependencies === 'object' && lock.dependencies !== null ? lock.dependencies : {})
        .filter(([packageName]) => NAMES.some(candidate => candidate === packageName)),
    ];
    return entries.flatMap(([, entry]: readonly [string, unknown]) =>
      typeof entry === 'object' && entry !== null && 'version' in entry && typeof entry.version === 'string' ? [entry.version] : []);
  }
  if (name === 'yarn.lock') {
    // Classic and Berry: a header naming the package's ranges, then its resolved version.
    return text.split(/\r?\n(?=\S)/).flatMap(block => {
      const [header = ''] = block.split(/\r?\n/, 1);
      if (!new RegExp(String.raw`(?:^|[\s",])"?${NAME}@`).test(header)) return [];
      const version = /^\s+version:?\s+"?([^"\s]+)"?\s*$/m.exec(block)?.[1];
      return version === undefined ? [] : [version];
    });
  }
  // pnpm (`/name@1.2.3:`, `'name@1.2.3':`, v5 `/name/1.2.3:`) and bun (`"name@1.2.3"`).
  const pattern = name === 'pnpm-lock.yaml'
    ? new RegExp(String.raw`^\s+['"]?\/?${NAME}[@/]${VERSION}`, 'gm')
    : new RegExp(String.raw`"${NAME}@${VERSION}"`, 'g');
  return [...text.matchAll(pattern)].map(match => match[2] ?? '');
}

/** The lockfiles that own a manifest directory's install: the closest directory that has any, up to the Git boundary. */
function ownerLockfiles(directory: string): readonly string[] {
  for (let current = resolve(directory); ; current = dirname(current)) {
    const found = LOCKFILES.map(name => join(current, name)).filter(file => existsSync(file) && lstatSync(file).isFile());
    if (found.length || existsSync(join(current, '.git')) || dirname(current) === current) return found;
  }
}

/**
 * Compare the project's Daytona 0.x version with the 0.x release SDK 2.x
 * bundles as `.v0`. Lockfiles say what is installed; a manifest without one
 * falls back to its range's lower bound. Older and newer-patch versions warn; a
 * newer minor refuses unless `allowNewer`, because its added APIs are in
 * neither `.v0` nor the mapping.
 */
export function checkV0Version(root: string, declared: readonly DeclaredDependency[], pinned: string, allowNewer: boolean): V0VersionCheck {
  const pin = parse(pinned);
  if (pin === null) return { warnings: [] };
  const observed = new Map<string, { readonly file: string; readonly version: SemVer }>();
  const unknown = new Set<string>();
  const lockfiles = new Map<string, readonly string[]>();
  for (const dependency of declared) {
    const directory = dirname(dependency.file);
    const owners = lockfiles.get(directory) ?? ownerLockfiles(directory);
    lockfiles.set(directory, owners);
    const locked = owners.flatMap(file => {
      let versions: readonly string[];
      // An unreadable or malformed lockfile is no version evidence.
      try { versions = lockedVersions(file); }
      catch (error) {
        if (error instanceof SyntaxError || error instanceof Error && 'code' in error) return [];
        throw error;
      }
      return versions.flatMap(value => { const version = zeroX(value); return version ? [{ file, version }] : []; });
    });
    if (locked.length) {
      for (const entry of locked) observed.set(`${entry.file}\0${entry.version.version}`, entry);
      continue;
    }
    if (owners.length) continue; // A lockfile resolving only 2.x (or nothing) for this package.
    const range = validRange(dependency.range) === null ? null : minVersion(dependency.range);
    if (range !== null && range.major >= 2) continue;
    const version = range === null || range.version === '0.0.0' ? undefined : zeroX(range.version);
    if (version) observed.set(`${dependency.file}\0${version.version}`, { file: dependency.file, version });
    else unknown.add(dependency.file);
  }
  const warnings: Warning[] = [];
  const order = (left: string, right: string): number => left < right ? -1 : left > right ? 1 : 0;
  const entries = [...observed.values()].map(entry => ({ ...entry, path: relativePath(root, entry.file) }))
    .sort((left, right) => order(left.path, right.path) || compare(left.version, right.version));
  for (const { path, version } of entries) {
    const relation = compare(version, pin);
    if (relation === 0) continue;
    if (relation < 0) warnings.push({ file: path, message: olderMessage(version.version, pinned) });
    else if (version.major > pin.major || version.minor > pin.minor) {
      if (!allowNewer) return { warnings, refusal: newerRefusal(version.version, path, pinned) };
      warnings.push({ file: path, message: allowedNewerMessage(version.version, pinned) });
    } else warnings.push({ file: path, message: newerPatchMessage(version.version, pinned) });
  }
  for (const file of [...unknown].sort((left, right) => order(relativePath(root, left), relativePath(root, right)))) warnings.push({ file: relativePath(root, file), message: unknownMessage(pinned) });
  return { warnings };
}
