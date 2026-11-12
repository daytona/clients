/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { contextUploadRuntime } from './contextUpload.js'
import { NotSupportedError } from './errors/DaytonaError.js'
import { badRequest } from './options.js'
import { contextEntries } from './contextEntries.js'
import { writeTar } from './contextTar.js'
import type { TarEntry } from './contextTar.js'
import { localSourcesOf } from './ImageBuilder.js'
import type { ImageBuilder } from './ImageBuilder.js'
import { decodeContextPath, utf8ContextPath } from './contextPath.js'
import { dockerfileEscape } from './builderText.js'
export { dockerfileEscape } from './builderText.js'

type Archive = { readonly path: string; cleanup(): Promise<void> }

async function nodeModules() {
  if (!contextUploadRuntime.isNode()) throw new NotSupportedError('local build contexts are only supported in Node.js', { statusCode: 501, code: 'NOT_IMPLEMENTED', source: 'SDK' })
  const fsSpecifier = 'node:fs/promises'
  const pathSpecifier = 'node:path'
  const osSpecifier = 'node:os'
  const fs: typeof import('node:fs/promises') = await import(/* @vite-ignore */ /* webpackIgnore: true */ fsSpecifier)
  const path: typeof import('node:path') = await import(/* @vite-ignore */ /* webpackIgnore: true */ pathSpecifier)
  const os: typeof import('node:os') = await import(/* @vite-ignore */ /* webpackIgnore: true */ osSpecifier)
  return { fs, path, os }
}

/** Preserve the existing tar-path handling outside Node and for non-directories. @internal */
export async function isContextDirectory(context: string): Promise<boolean> {
  if (!contextUploadRuntime.isNode()) return false
  utf8ContextPath(context)
  const { fs } = await nodeModules()
  // Any stat failure means "not a directory here": the tar-path upload then
  // reports its own typed error, as it did before directories were accepted.
  try {
    return (await fs.stat(context)).isDirectory()
  } catch {
    return false
  }
}

async function archive(entries: readonly TarEntry[]): Promise<Archive> {
  const { fs, path, os } = await nodeModules()
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'daytona-context-'))
  const cleanup = (): Promise<void> => fs.rm(directory, { recursive: true, force: true })
  let completed = false
  try {
    const filename = path.join(directory, 'context.tar')
    const output = await fs.open(filename, 'wx', 0o600)
    try { await writeTar(output, entries) } finally { await output.close() }
    completed = true
    return { path: filename, cleanup }
  } finally {
    if (!completed) await cleanup()
  }
}

/** Build a temporary deterministic tar from a builder's local sources. @internal */
export async function archiveBuilder(builder: ImageBuilder): Promise<Archive> {
  const dockerfile = builder.toDockerfile()
  if (localSourcesOf(builder).length > 0) {
    // Appended COPY basenames are escaped for the default Dockerfile lexer.
    if (dockerfileEscape(dockerfile) !== '\\') throw badRequest('local files need the default Dockerfile escape character')
  }
  const { fs } = await nodeModules()
  const entries: TarEntry[] = [{ kind: 'bytes', name: 'Dockerfile', mode: 0o644, content: new TextEncoder().encode(dockerfile) }]
  const parents = new Set<string>()
  for (const source of localSourcesOf(builder)) {
    const components = source.archivePath.split('/')
    for (let i = 1; i < components.length; i++) {
      const name = components.slice(0, i).join('/')
      if (!parents.has(name)) { parents.add(name); entries.push({ kind: 'dir', name, mode: 0o755 }) }
    }
    // The source itself is followed, as `docker build` follows a named path:
    // a symlinked requirements file is copied as its content, not as a link
    // that would dangle under `.daytona-context/<n>/`. Links inside an added
    // directory are archived as links (contextEntries).
    let real: string
    let realBytes: Buffer
    let info: import('node:fs').Stats
    try {
      realBytes = await fs.realpath(source.localPath, { encoding: 'buffer' })
      real = decodeContextPath(realBytes)
      info = await fs.stat(real)
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'code' in error && (error.code === 'ENOENT' || error.code === 'ENOTDIR' || error.code === 'ELOOP')) {
        throw badRequest(`local source does not exist: ${JSON.stringify(source.localPath)}`)
      }
      throw error
    }
    // A path that was not a link when it was added and is one now, or whose
    // real path moved (an ancestor swapped for a link), has been redirected:
    // refused, so a swap between declaration and build cannot pull in another
    // tree (as Python refuses it).
    if (!source.link && (!realBytes.equals(source.real) || await fs.lstat(source.localPath).then((stats) => stats.isSymbolicLink(), () => false))) {
      throw badRequest(`local source changed since it was added: ${JSON.stringify(source.localPath)}`)
    }
    switch (source.kind) {
      case 'file':
        if (!info.isFile()) throw badRequest(`local source is not a regular file: ${JSON.stringify(source.localPath)}`)
        entries.push({ kind: 'file', name: source.archivePath, localPath: real, mode: info.mode, size: info.size, dev: info.dev, ino: info.ino })
        break
      case 'dir': {
        if (!info.isDirectory()) throw badRequest(`local source is not a directory: ${JSON.stringify(source.localPath)}`)
        entries.push({ kind: 'dir', name: source.archivePath, mode: info.mode })
        const children = await contextEntries(real)
        for (const child of children) entries.push({ ...child, name: `${source.archivePath}/${child.name}` })
        break
      }
      default: assertNever(source.kind)
    }
  }
  return archive(entries)
}

/** Archive a Docker build context with its Dockerfile-specific ignore rules. @internal */
export async function archiveDirectory(context: string, dockerfile: string): Promise<Archive> {
  await nodeModules()
  return archive(await contextEntries(context, dockerfile))
}

function assertNever(value: never): never { throw badRequest(`unsupported local source ${String(value)}`) }
