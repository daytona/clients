/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { compare, minVersion, parse, validRange } from 'semver';
import type { SemVer } from 'semver';
import { parse as parseYaml } from 'yaml';
import type { Warning } from './model.js';
import { relativePath } from './discovery.js';

/**
 * A Daytona 0.x dependency a manifest declares: the dependency key (the alias
 * for `npm:` aliases), its literal spec, the version range the spec names, and
 * the declaring package's own name.
 */
export type DeclaredDependency = {
  readonly file: string; readonly key: string; readonly spec: string; readonly range: string; readonly owner?: string | undefined;
};

export type V0VersionCheck = { readonly warnings: readonly Warning[]; readonly refusal?: string };

// Closest first, like the package managers: the nearest directory with a lockfile owns the install.
const LOCKFILES = ['package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lock'] as const;

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

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? Object.fromEntries(Object.entries(value)) : undefined;
}

/** The version at the end of a lockfile reference: `0.224.0`, `/@daytonaio/sdk/0.224.0`, `@daytonaio/sdk@0.224.0(peer@1)`, `npm:@daytonaio/sdk@0.224.0`. */
function versionIn(reference: string): string | undefined {
  const bare = reference.replace(/\(.*$/, '').replace(/_.*$/, '').trim();
  return /(?:^|[@/])(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$/.exec(bare)?.[1];
}

/** What one lockfile resolves for one declared dependency, by package manager. */
type Lock = (dependency: DeclaredDependency, importer: string) => readonly string[];

function npmLock(text: string): Lock {
  const lock = record(JSON.parse(text)) ?? {};
  const packages = record(lock['packages']);
  const legacy = record(lock['dependencies']) ?? {};
  const version = (entry: unknown): readonly string[] => {
    const value = record(entry)?.['version'];
    const found = typeof value === 'string' ? versionIn(value) : undefined;
    return found === undefined ? [] : [found];
  };
  return ({ key }, importer) => {
    // lockfileVersion 1 records the root project's tree only.
    if (packages === undefined) return importer === '' ? version(legacy[key]) : [];
    // A workspace's own copy wins; otherwise it resolves the hoisted root copy.
    const own = importer === '' ? undefined : packages[`${importer}/node_modules/${key}`];
    return version(own ?? packages[`node_modules/${key}`]);
  };
}

function pnpmLock(text: string): Lock {
  const lock = record(parseYaml(text)) ?? {};
  const importers = record(lock['importers']);
  return ({ key }, importer) => {
    // pnpm 5 / 6 single-project lockfiles keep the root's sections at the top.
    const project = importers === undefined ? (importer === '' ? lock : undefined) : record(importers[importer === '' ? '.' : importer]);
    return ['dependencies', 'devDependencies', 'optionalDependencies'].flatMap(section => {
      const entry = record(project?.[section])?.[key];
      const reference = typeof entry === 'string' ? entry : record(entry)?.['version'];
      const found = typeof reference === 'string' ? versionIn(reference) : undefined;
      return found === undefined ? [] : [found];
    });
  };
}

function yarnLock(text: string): Lock {
  // Classic and Berry: a header listing the descriptors that resolved here, then the version.
  const blocks = text.split(/\r?\n(?=\S)/).flatMap(block => {
    const [header = ''] = block.split(/\r?\n/, 1);
    if (!header.endsWith(':') || header.startsWith('#')) return [];
    const version = /^\s+version:?\s+"?([^"\s]+)"?\s*$/m.exec(block)?.[1];
    if (version === undefined) return [];
    const descriptors = header.slice(0, -1).split(/,\s*/).map(descriptor => descriptor.trim().replace(/^"|"$/g, ''));
    return [{ descriptors, version }];
  });
  return ({ key, spec }) => {
    const wanted = new Set([`${key}@${spec}`, `${key}@npm:${spec}`]);
    return blocks.filter(block => block.descriptors.some(descriptor => wanted.has(descriptor))).map(block => block.version);
  };
}

function bunLock(text: string): Lock {
  // `"<parent>/<key>": ["<name>@<version>", …]`: an empty parent is the hoisted copy.
  const entries = new Map<string, string>();
  for (const match of text.matchAll(/"([^"\n]+)"\s*:\s*\[\s*"([^"\n]+)"/g)) {
    const version = versionIn(match[2] ?? '');
    if (version !== undefined) entries.set(match[1] ?? '', version);
  }
  return ({ key, owner }, importer) => {
    const own = importer !== '' && owner !== undefined ? entries.get(`${owner}/${key}`) : undefined;
    const version = own ?? entries.get(key);
    return version === undefined ? [] : [version];
  };
}

const READERS: Readonly<Record<(typeof LOCKFILES)[number], (text: string) => Lock>> = {
  'package-lock.json': npmLock, 'npm-shrinkwrap.json': npmLock, 'yarn.lock': yarnLock, 'pnpm-lock.yaml': pnpmLock, 'bun.lock': bunLock,
};

function isFile(file: string): boolean {
  // Follows symlinks, like the Python tool's Path.is_file().
  try { return statSync(file).isFile(); }
  catch (error) {
    if (error instanceof Error && 'code' in error) return false;
    throw error;
  }
}

/** The lockfiles that own a manifest directory's install: the closest directory that has any, up to the Git boundary. */
function ownerLockfiles(directory: string): readonly string[] {
  for (let current = resolve(directory); ; current = dirname(current)) {
    const found = LOCKFILES.map(name => join(current, name)).filter(isFile);
    if (found.length || existsSync(join(current, '.git')) || dirname(current) === current) return found;
  }
}

function zeroX(value: string): SemVer | undefined {
  const version = parse(value);
  return version !== null && version.major === 0 ? version : undefined;
}

/**
 * Compare the project's Daytona 0.x version with the 0.x release SDK 2.x
 * bundles as `.v0`. The owning lockfile's resolution for the declaring
 * manifest says what is installed; without one, the manifest range's lower
 * bound stands in. Older and newer-patch versions warn; a newer minor refuses
 * unless `allowNewer`, because its added APIs are in neither `.v0` nor the
 * mapping.
 */
export function checkV0Version(root: string, declared: readonly DeclaredDependency[], pinned: string, allowNewer: boolean): V0VersionCheck {
  const pin = parse(pinned);
  if (pin === null) return { warnings: [] };
  const observed = new Map<string, { readonly file: string; readonly version: SemVer }>();
  const unknown = new Set<string>();
  const owners = new Map<string, readonly string[]>();
  const locks = new Map<string, Lock | undefined>();
  const lockFor = (file: string): Lock | undefined => {
    if (!locks.has(file)) {
      let lock: Lock | undefined;
      // An unreadable or malformed lockfile is no version evidence.
      const name = LOCKFILES.find(candidate => join(dirname(file), candidate) === file);
      try { lock = name === undefined ? undefined : READERS[name](readFileSync(file, 'utf8').replace(/^\uFEFF/, '')); }
      catch (error) {
        if (!(error instanceof SyntaxError || error instanceof Error && ('code' in error || error.name === 'YAMLParseError'))) throw error;
      }
      locks.set(file, lock);
    }
    return locks.get(file);
  };
  for (const dependency of declared) {
    const directory = dirname(dependency.file);
    const lockfiles = owners.get(directory) ?? ownerLockfiles(directory);
    owners.set(directory, lockfiles);
    const resolved = lockfiles.flatMap(file => {
      const importer = relative(dirname(file), directory).split(sep).join('/');
      return (lockFor(file)?.(dependency, importer) ?? []).map(version => ({ file, version }));
    });
    if (resolved.length) {
      // A resolution outside 0.x (an already upgraded project) needs no check.
      for (const { file, version } of resolved) {
        const zero = zeroX(version);
        if (zero) observed.set(`${file}\0${zero.version}`, { file, version: zero });
      }
      continue;
    }
    // No lockfile, or one that does not resolve this dependency: the manifest range stands in.
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
  for (const file of [...unknown].sort((left, right) => order(relativePath(root, left), relativePath(root, right)))) {
    warnings.push({ file: relativePath(root, file), message: unknownMessage(pinned) });
  }
  return { warnings };
}
