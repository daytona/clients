/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { join, posix } from 'node:path';
import { Project } from 'ts-morph';
import { relativePath, text } from './discovery.js';
import type { ReadContext } from './read-failures.js';

function dependencyEntries(files: readonly string[], context: ReadContext): readonly (readonly [string, unknown])[] {
  return files.filter(file => posix.basename(file.replaceAll('\\', '/')) === 'package.json').flatMap(file => {
    const value = manifest(text(file, context.failures));
    return ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'].flatMap(section => {
      const entries = value?.[section];
      return isRecord(entries) ? Object.entries(entries) : [];
    });
  });
}

/** Local protocols are project modules, not external registry dependencies. */
export function dependencyPackageNames(files: readonly string[], context: ReadContext): ReadonlySet<string> {
  const entries = dependencyEntries(files, context);
  const local = new Set(entries.filter(([, spec]) => isLocalSpec(spec)).map(([name]) => name));
  return new Set(entries.filter(([name]) => !local.has(name)).map(([name]) => name));
}

function isLocalSpec(spec: unknown): boolean {
  return typeof spec === 'string' && /^(?:workspace|file|link):/.test(spec);
}

/** Resolve workspace globs once against the existing discovery, not per source. */
export function workspacePackageNames(files: readonly string[], context: ReadContext): readonly string[] {
  const rootManifest = join(context.root, 'package.json');
  const root = files.includes(rootManifest) ? manifest(text(rootManifest, context.failures)) : undefined;
  const workspaces = root?.['workspaces'];
  const patterns = [...strings(workspaces), ...strings(isRecord(workspaces) ? workspaces['packages'] : undefined)];
  const pnpm = join(context.root, 'pnpm-workspace.yaml');
  if (files.includes(pnpm)) patterns.push(...pnpmPackages(text(pnpm, context.failures)));
  const local = dependencyEntries(files, context).filter(([, spec]) => isLocalSpec(spec)).map(([name]) => name);
  if (!patterns.length) return local;
  // The in-memory host reuses ts-morph's glob semantics (including exclusions
  // and brace patterns) without another recursive disk search.
  const host = new Project({ useInMemoryFileSystem: true }).getFileSystem();
  const packages = files.filter(file => posix.basename(file.replaceAll('\\', '/')) === 'package.json');
  for (const file of packages) host.writeFileSync(`/${relativePath(context.root, file)}`, '');
  const globs = patterns.map(pattern => {
    const excluded = pattern.startsWith('!');
    const path = pattern.slice(excluded ? 1 : 0).replace(/^\.\//, '').replace(/\/$/, '');
    return `${excluded ? '!' : ''}/${path}/package.json`;
  });
  const selected = new Set(host.globSync(globs));
  return [...local, ...packages.flatMap(file => {
    if (!selected.has(`/${relativePath(context.root, file)}`)) return [];
    const name = manifest(text(file, context.failures))?.['name'];
    return typeof name === 'string' ? [name] : [];
  })];
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function strings(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((entry: unknown): entry is string => typeof entry === 'string') : [];
}
function manifest(input: string): Readonly<Record<string, unknown>> | undefined {
  try {
    const value: unknown = JSON.parse(input);
    return isRecord(value) ? value : undefined;
  }
  catch (error) { if (error instanceof SyntaxError) return undefined; throw error; }
}
function pnpmPackages(input: string): readonly string[] {
  const lines = input.split(/\r?\n/);
  const start = lines.findIndex(line => /^packages:/.test(line));
  if (start < 0) return [];
  const scalar = /"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|#[^\r\n]*|[^\s,\[\]#]+|\]/g;
  const inline = lines[start]?.slice('packages:'.length).trim() ?? '';
  const values: string[] = [];
  if (inline.startsWith('[')) {
    const flow = [inline.slice(1), ...lines.slice(start + 1)].join('\n');
    for (const value of flow.match(scalar) ?? []) {
      if (value === ']') break;
      if (!value.startsWith('#')) values.push(value);
    }
  }
  else for (const line of lines.slice(start + 1)) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue;
    const entry = line.match(/^[\t ]*-[\t ]+(.+?)\s*$/)?.[1];
    if (entry === undefined) break;
    values.push(...entry.match(scalar)?.slice(0, 1) ?? []);
  }
  return values.map(value => value.startsWith('"') ? JSON.parse(value) : value.startsWith("'") ? value.slice(1, -1).replaceAll("''", "'") : value)
    .filter((value: unknown): value is string => typeof value === 'string');
}
