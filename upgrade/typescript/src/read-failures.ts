/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import type { FileSystemHost } from 'ts-morph';
import { relative, resolve } from 'node:path';
import type { Item } from './model.js';

export type ReadContext = { readonly root: string; readonly failures?: ReadFailures };
export function readContext(context: string | ReadContext): ReadContext {
  return typeof context === 'string' ? { root: context } : context;
}

// One run owns this accumulator; repeated reads of a path produce one item.
export class ReadFailures {
  private readonly failures = new Map<string, Item>();
  constructor(private readonly root: string) {}

  get items(): readonly Item[] { return [...this.failures.values()]; }
  has(path: string): boolean { return this.failures.has(resolve(path)); }

  read<T>(path: string, operation: () => T, probe = false): T | undefined {
    try { return operation(); }
    catch (error) {
      if (!(error instanceof Error)) throw error;
      if (probe && 'code' in error && error.code === 'ENOENT') throw error;
      const file = relative(this.root, path).replaceAll('\\', '/') || '.';
      const permission = 'code' in error && (error.code === 'EACCES' || error.code === 'EPERM');
      const description = permission
        ? 'Permission denied' : error.message.replace(/^[A-Z][A-Z0-9_]*:\s*([^,]+).*$/,
          (_message: string, detail: string) => detail.charAt(0).toUpperCase() + detail.slice(1));
      this.failures.set(resolve(path), { file, line: 0, column: 0, kind: 'unsupported-file',
        message: `${file} could not be read: ${description}; ${permission ? 'fix its permissions' : 'fix the problem'}, then rerun.` });
      return undefined;
    }
  }

  fileSystem(): FileSystemHost {
    const host = new Project().getFileSystem();
    const readFile = host.readFileSync.bind(host);
    const readDirectory = host.readDirSync.bind(host);
    // Compiler resolution probes missing paths normally. Keep those exceptions
    // intact, but record actual failed reads and allow sibling analysis to run.
    host.readFileSync = (path, encoding) => this.read(path, () => readFile(path, encoding), true) ?? '';
    host.readDirSync = path => this.read(path, () => readDirectory(path), true) ?? [];
    return host;
  }
}
