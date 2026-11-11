/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Dirent, Stats } from 'node:fs'
import type { Results } from './dockerignore.js'
import { contextUploadRuntime } from './contextUpload.js'
import { NotSupportedError } from './errors/DaytonaError.js'
import { badRequest } from './options.js'
import { decodeContextPath, utf8ContextPath } from './contextPath.js'

/** @internal */
export interface ArchiveEntry {
  readonly rel: string
  readonly current: string
  readonly info: Stats
}

/** @internal */
export interface ArchiveDecision {
  readonly keep: boolean
  readonly descend: boolean
  readonly state: Results | undefined
}

/** @internal */
export interface ArchiveFilter {
  keep(rel: string, isDir: boolean, parent?: Results): ArchiveDecision
}

/** Archive-order walk; ignored parents are emitted only before a kept descendant. @internal */
export async function walkArchiveEntries(root: string, filter?: ArchiveFilter, visit?: (entry: ArchiveEntry) => Promise<void>): Promise<readonly ArchiveEntry[]> {
  if (!contextUploadRuntime.isNode()) throw new NotSupportedError('local build contexts require Node.js', { statusCode: 501, code: 'NOT_IMPLEMENTED', source: 'SDK' })
  const fsSpecifier = 'node:fs/promises'
  const pathSpecifier = 'node:path'
  const fs: typeof import('node:fs/promises') = await import(/* @vite-ignore */ /* webpackIgnore: true */ fsSpecifier)
  const path: typeof import('node:path') = await import(/* @vite-ignore */ /* webpackIgnore: true */ pathSpecifier)
  const absolute = path.resolve(utf8ContextPath(root))
  if (!(await fs.stat(absolute)).isDirectory()) throw badRequest(`${JSON.stringify(root)} is not a directory`)
  const entries: ArchiveEntry[] = []
  // These stack frames are traversal accumulators, not shared model state.
  const open: { readonly rel: string; readonly current: string; readonly state: Results | undefined; reported: boolean }[] = []
  const walk = async (directory: string, kept: boolean): Promise<void> => {
    // Bun ignores withFileTypes when encoding is 'buffer' and returns plain
    // Buffers: those become entries of unknown type, decided by lstat below.
    let names: { readonly name: Buffer; readonly dirent?: Dirent<Buffer> }[]
    try {
      const listed: readonly (Dirent<Buffer> | Buffer)[] = await fs.readdir(directory, { withFileTypes: true, encoding: 'buffer' })
      names = listed.map((item) => Buffer.isBuffer(item) ? { name: item } : { name: item.name, dirent: item })
    }
    catch (error) {
      if (typeof error === 'object' && error !== null && 'code' in error && (error.code === 'EACCES' || error.code === 'EPERM')) {
        if (!kept) return
        throw badRequest(`build context directory is not readable: ${JSON.stringify(directory)}`)
      }
      throw error
    }
    // Go sorts directory names by UTF-8 bytes, not the host locale or UTF-16.
    names.sort((left, right) => left.name.compare(right.name))
    for (const name of names) {
      // Decoded per entry, so an undecodable name is reported in walk order
      // (as Python does), not before the subtrees of earlier siblings.
      const current = decodeContextPath(Buffer.concat([Buffer.from(directory + path.sep), name.name]))
      const rel = path.relative(absolute, current).split(path.sep).join('/')
      // Node documents that a Dirent type can be unknown on some filesystems:
      // fall back to lstat then. An ignored entry that vanished meanwhile is
      // just skipped, as Python skips what it does not keep.
      let isDir: boolean
      const dirent = name.dirent
      if (dirent !== undefined && (dirent.isDirectory() || dirent.isFile() || dirent.isSymbolicLink() || dirent.isFIFO() || dirent.isSocket() || dirent.isBlockDevice() || dirent.isCharacterDevice())) isDir = dirent.isDirectory()
      else {
        try { isDir = (await fs.lstat(current)).isDirectory() }
        catch (error) {
          if (typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT') continue
          throw error
        }
      }
      const decision = filter?.keep(rel, isDir, open[open.length - 1]?.state) ?? { keep: true, descend: true, state: undefined }
      if (decision.keep) {
        for (const parent of open) {
          if (!parent.reported) {
            const entry = { rel: parent.rel, current: parent.current, info: await fs.lstat(parent.current) }
            if (visit === undefined) entries.push(entry)
            else await visit(entry)
            parent.reported = true
          }
        }
        const entry = { rel, current, info: await fs.lstat(current) }
        // Each kept entry is checked as it is reached, so the first refusal
        // is the first in walk order in both SDKs.
        if (visit === undefined) entries.push(entry)
        else await visit(entry)
      }
      if (isDir && (decision.keep || decision.descend)) {
        open.push({ rel, current, state: decision.state, reported: decision.keep })
        await walk(current, decision.keep)
        open.pop()
      }
    }
  }
  await walk(absolute, true)
  return entries
}
