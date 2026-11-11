/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Filter, Matcher, readPatterns, walkArchiveEntries } from './dockerignore.js'
import type { TarEntry } from './contextTar.js'
import { badRequest } from './options.js'
import { decodeContextPath, utf8ContextPath } from './contextPath.js'

/** Resolve Docker's ignore-file precedence and validate entries without following links. @internal */
export async function contextEntries(root: string, dockerfile?: string): Promise<readonly TarEntry[]> {
  const fsSpecifier = 'node:fs/promises'
  const pathSpecifier = 'node:path'
  const fs: typeof import('node:fs/promises') = await import(/* @vite-ignore */ /* webpackIgnore: true */ fsSpecifier)
  const path: typeof import('node:path') = await import(/* @vite-ignore */ /* webpackIgnore: true */ pathSpecifier)
  const absolute = decodeContextPath(await fs.realpath(path.resolve(utf8ContextPath(root)), { encoding: 'buffer' }))
  const realRoot = absolute
  const regularFile = async (relative: string): Promise<string> => {
    const filename = path.join(absolute, relative)
    const resolved = decodeContextPath(await fs.realpath(filename, { encoding: 'buffer' }))
    const inside = path.relative(realRoot, resolved)
    if (inside === '..' || inside.startsWith(`..${path.sep}`)) throw badRequest(`build context file leaves its local source: ${JSON.stringify(filename)}`)
    if (!(await fs.stat(filename)).isFile()) throw badRequest(`build context path is not a regular file: ${JSON.stringify(filename)}`)
    return filename
  }
  let selected = '.dockerignore'
  let dockerfileName: string | undefined
  if (dockerfile !== undefined) {
    if (dockerfile.includes('\0') || path.isAbsolute(dockerfile) || path.win32.isAbsolute(dockerfile)) throw badRequest('dockerfile must be a relative path inside the build context')
    // Lexical, as Python and the CLI check it: resolving against the real
    // root would let `../<root name>/Dockerfile` re-enter through its name.
    const lexical = path.posix.normalize(dockerfile.split(path.sep).join('/'))
    if (lexical === '.' || lexical === '..' || lexical.startsWith('../')) throw badRequest('dockerfile must be a relative path inside the build context')
    const relative = path.relative(absolute, path.resolve(absolute, dockerfile))
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || relative === '') throw badRequest('dockerfile must be a relative path inside the build context')
    dockerfileName = relative.split(path.sep).join('/')
    try { await regularFile(relative) }
    catch (error) {
      if (typeof error === 'object' && error !== null && 'code' in error && (error.code === 'ENOENT' || error.code === 'ENOTDIR' || error.code === 'ELOOP')) {
        throw badRequest(`dockerfile ${JSON.stringify(dockerfile)} does not exist in the build context`)
      }
      throw error
    }
    try {
      await fs.access(path.join(absolute, dockerfileName + '.dockerignore'))
      selected = dockerfileName + '.dockerignore'
    } catch (error) {
      if (!(typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT')) throw error
    }
  }
  let patterns: readonly string[] = []
  try { patterns = readPatterns(await fs.readFile(await regularFile(selected), 'utf8')) }
  catch (error) {
    if (!(typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT')) throw error
  }
  const always = dockerfileName === undefined ? [] : [dockerfileName, '.dockerignore', selected]
  const filter = new Filter(new Matcher(patterns), always)
  const entries: TarEntry[] = []
  await walkArchiveEntries(absolute, filter, async (entry) => {
    if (dockerfileName === undefined && entry.rel === '.dockerignore') return
    const base = { name: entry.rel, mode: entry.info.mode }
    if (entry.info.isFile()) entries.push({ ...base, kind: 'file', localPath: entry.current, size: entry.info.size, dev: entry.info.dev, ino: entry.info.ino })
    else if (entry.info.isDirectory()) entries.push({ ...base, kind: 'dir' })
    else if (entry.info.isSymbolicLink()) {
      // Read as bytes: a target that is not UTF-8 is refused, not replaced.
      const target = decodeContextPath(await fs.readlink(entry.current, { encoding: 'buffer' }))
      // Lexical, relative to the source (as Python): resolving against the
      // host path would let `../../<root name>/x` re-enter through the root's
      // own name, which differs between machines.
      const relative = path.posix.normalize(path.posix.join(path.posix.dirname(entry.rel), target))
      if (path.isAbsolute(target) || relative === '..' || relative.startsWith('../')) {
        throw badRequest(`symbolic link leaves its local source: ${JSON.stringify(entry.current)}`)
      }
      entries.push({ ...base, kind: 'symlink', target })
    } else throw badRequest(`unsupported file type in build context: ${JSON.stringify(entry.current)}`)
  })
  return entries
}
