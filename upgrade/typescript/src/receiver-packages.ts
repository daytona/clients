/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { Node } from 'ts-morph';
import type { Type } from 'ts-morph';
import { receiverConstraints } from './receiver-constraints.js';

/** Declaration paths are relative to the SDK package, never to the project. */
type PackageIdentity = { readonly name: string; readonly major: number; readonly path: string };

export class ReceiverPackages {
  private readonly versions = new Map<string, number>();
  private readonly roots = new Map<string, string | undefined>();

  packageRoot(path: string): string | undefined {
    const normalized = path.replaceAll('\\', '/');
    const installed = normalized.match(/^(.*\/node_modules\/(?:@daytonaio\/sdk|@daytona\/sdk))\//)?.[1];
    if (installed) return installed;
    const start = dirname(normalized);
    if (this.roots.has(start)) return this.roots.get(start);
    let directory = start;
    while (true) {
      const manifest = join(directory, 'package.json');
      if (existsSync(manifest)) {
        let data: unknown;
        try { data = JSON.parse(readFileSync(manifest, 'utf8')); }
        catch (error) {
          if (!(error instanceof SyntaxError)) throw error;
          this.roots.set(start, undefined);
          return undefined;
        }
        if (typeof data === 'object' && data !== null && 'name' in data) {
          const root = data.name === '@daytonaio/sdk' || data.name === '@daytona/sdk' ? directory : undefined;
          this.roots.set(start, root);
          return root;
        }
      }
      const parent = dirname(directory);
      if (parent === directory) break;
      directory = parent;
    }
    this.roots.set(start, undefined);
    return undefined;
  }

  relativePath(path: string): string {
    const normalized = path.replaceAll('\\', '/');
    const root = this.packageRoot(normalized);
    return root === undefined ? normalized : normalized.slice(root.length);
  }

  version(path: string): number | undefined {
    const directory = this.packageRoot(path);
    if (!directory) return undefined;
    const cached = this.versions.get(directory);
    if (cached !== undefined) return cached;
    const manifest = join(directory, 'package.json');
    if (!existsSync(manifest)) return undefined;
    const data: unknown = JSON.parse(readFileSync(manifest, 'utf8'));
    if (typeof data !== 'object' || data === null || !('version' in data) || typeof data.version !== 'string') return undefined;
    const major = Number.parseInt(data.version.split('.')[0] ?? '', 10);
    this.versions.set(directory, major);
    return major;
  }

  identity(node: Node, classOnly = false): PackageIdentity | undefined {
    return this.identityType(node.getType(), classOnly);
  }

  identityType(value: Type, classOnly = false): PackageIdentity | undefined {
    const type = value.getNonNullableType();
    const pending = [...receiverConstraints(type)];
    const seen = new Set<typeof type>();
    for (const candidate of pending) {
      if (seen.has(candidate)) continue;
      seen.add(candidate);
      if (classOnly && !candidate.getSymbol()?.getDeclarations().some(Node.isClassDeclaration)) continue;
      for (const symbol of [candidate.getSymbol(), candidate.getAliasSymbol()]) {
        for (const declaration of (symbol?.getAliasedSymbol() ?? symbol)?.getDeclarations() ?? []) {
          const path = declaration.getSourceFile().getFilePath().replaceAll('\\', '/');
          const major = this.version(path);
          if (major === undefined || !symbol) continue;
          return { name: symbol.getName(), major, path: this.relativePath(path) };
        }
      }
      pending.push(...candidate.getBaseTypes());
    }
    return undefined;
  }
}
