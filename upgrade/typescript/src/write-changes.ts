/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import { basename, dirname, join } from 'node:path';
import type { Change } from './model.js';

type RollbackFailure = { readonly file: string; readonly error: Error };
export class WriteFailure extends Error {
  override readonly name = 'WriteFailure';
  constructor(readonly file: string, readonly failure: Error, readonly rollbackFailures: readonly RollbackFailure[]) {
    const recovery = rollbackFailures.length ? `Rollback failed: ${rollbackFailures.map(entry => `${entry.file}: ${entry.error.message}`).join('; ')}` :
      'All changes were rolled back.';
    super(`Failed to write ${file}: ${failure.message}. ${recovery}`, { cause: failure });
  }
}
function originalBytes(file: string): Buffer | null {
  try { return fs.readFileSync(file); }
  catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return null;
    throw error;
  }
}
export function writeChanges(changes: readonly Change[]): void {
  const originals = changes.map(change => {
    const before = originalBytes(change.file);
    return { file: change.file, before, after: change.after,
      mode: before === null ? 0o666 & ~process.umask() : fs.statSync(change.file).mode & 0o7777 };
  });
  const written: typeof originals = [];
  const temporary = new Set<string>();
  const failures: RollbackFailure[] = [];
  let failedFile = '';
  let failure: Error | undefined;
  const prepare = (target: typeof originals[number], bytes: string | Buffer): string => {
    const file = join(dirname(target.file), `.${basename(target.file)}.${randomUUID()}.daytona-upgrade.tmp`);
    const descriptor = fs.openSync(file, 'wx', 0o600);
    temporary.add(file);
    try {
      fs.writeFileSync(descriptor, bytes);
      // fchmod the open descriptor: the path could be swapped for a symlink.
      fs.fchmodSync(descriptor, target.mode);
      fs.fsyncSync(descriptor);
    } finally { fs.closeSync(descriptor); }
    return file;
  };
  try {
    const prepared = originals.map(target => {
      failedFile = target.file;
      return { target, temporary: prepare(target, target.after) };
    });
    for (const entry of prepared) {
      failedFile = entry.target.file;
      fs.renameSync(entry.temporary, entry.target.file);
      temporary.delete(entry.temporary);
      written.push(entry.target);
    }
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    failure = error;
    for (const original of [...written].reverse()) {
      try {
        if (original.before === null) fs.rmSync(original.file, { force: true });
        else {
          const file = prepare(original, original.before);
          fs.renameSync(file, original.file);
          temporary.delete(file);
        }
      } catch (rollbackError) {
        if (!(rollbackError instanceof Error)) throw rollbackError;
        failures.push({ file: original.file, error: rollbackError });
      }
    }
  } finally {
    for (const file of temporary) {
      try { fs.rmSync(file, { force: true }); }
      catch (error) {
        if (!(error instanceof Error)) throw error;
        failures.push({ file, error });
      }
    }
  }
  if (failure) throw new WriteFailure(failedFile, failure, failures);
}
