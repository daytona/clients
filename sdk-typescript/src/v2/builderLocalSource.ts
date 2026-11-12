/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { contextUploadRuntime } from './contextUpload.js'
import { NotSupportedError } from './errors/DaytonaError.js'
import { badRequest } from './options.js'
import { assertNever, assertText } from './builderText.js'
import { utf8ContextPath } from './contextPath.js'

/** @internal */
export type LocalSource = {
  readonly localPath: string
  readonly kind: 'file' | 'dir'
  readonly archivePath: string
  /** Whether the declared path itself was a symbolic link (see contextArchive). */
  readonly link: boolean
  /** Its real path when added, as bytes (empty for a link): a non-link source whose real path differs at build time had an ancestor swapped (see contextArchive). */
  readonly real: Uint8Array
}

/** @internal */
export function expandHome(path: string): string {
  utf8ContextPath(path)
  const os = process.getBuiltinModule('node:os')
  const pathModule = process.getBuiltinModule('node:path')
  return path === '~' || path.startsWith('~/') || (pathModule.sep === '\\' && path.startsWith('~\\'))
    ? os.homedir() + path.slice(1)
    : path
}

/** @internal */
export function localSource(localPath: string, kind: LocalSource['kind'], position: number): LocalSource {
  if (!contextUploadRuntime.isNode()) {
    throw new NotSupportedError('ImageBuilder local files are only supported in Node.js', { statusCode: 501, code: 'NOT_IMPLEMENTED', source: 'SDK' })
  }
  const fs = process.getBuiltinModule('node:fs')
  const path = process.getBuiltinModule('node:path')
  if (typeof localPath === 'string') utf8ContextPath(localPath)
  const given = assertText(localPath, 'ImageBuilder localPath')
  if (given.includes('\0')) throw badRequest('ImageBuilder localPath must not contain a NUL character')
  const absolute = path.resolve(expandHome(given))
  let info: ReturnType<typeof fs.statSync>
  try {
    info = fs.statSync(absolute, { throwIfNoEntry: false })
  } catch (error) {
    // Filesystem errors can originate in another realm; narrow by their OS code.
    if (typeof error === 'object' && error !== null && 'code' in error && (error.code === 'ENOTDIR' || error.code === 'ELOOP')) {
      throw badRequest(`${absolute} does not exist`)
    }
    if (typeof error === 'object' && error !== null && 'code' in error && (error.code === 'EACCES' || error.code === 'EPERM')) {
      throw badRequest(`build context file is not readable: ${JSON.stringify(absolute)}`)
    }
    throw error
  }
  if (info === undefined) throw badRequest(`${absolute} does not exist`)
  switch (kind) {
    case 'file':
      if (!info.isFile()) throw badRequest(`${absolute} is not a file`)
      break
    case 'dir':
      if (!info.isDirectory()) throw badRequest(`${absolute} is not a directory`)
      break
    default:
      return assertNever(kind)
  }
  const archiveRoot = `.daytona-context/${position}`
  const basename = path.basename(absolute)
  // COPY sources go through BuildKit's shell-word lexer (variables, quotes)
  // and glob matching, even in JSON form.
  const filename = /[\x00-\x1f\x7f-\x9f$\\*?\[\]'"]/.test(basename) ? 'file' : basename
  const link = fs.lstatSync(absolute, { throwIfNoEntry: false })?.isSymbolicLink() ?? false
  // Native, as the build's fs.promises.realpath; a followed link needs none.
  const real = link ? new Uint8Array() : fs.realpathSync.native(absolute, { encoding: 'buffer' })
  return Object.freeze({ localPath: absolute, kind, archivePath: kind === 'file' ? `${archiveRoot}/${filename}` : archiveRoot, link, real })
}
