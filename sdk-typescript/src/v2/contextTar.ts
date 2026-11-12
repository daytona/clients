/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { FileHandle } from 'node:fs/promises'
import { badRequest } from './options.js'
import { MAX_CONTEXT_BYTES } from './contextUpload.js'

/** @internal */
export type TarEntry = {
  readonly name: string
  readonly mode: number
} & (
  | { readonly kind: 'file'; readonly localPath: string; readonly size: number; readonly dev: number; readonly ino: number }
  | { readonly kind: 'bytes'; readonly content: Uint8Array }
  | { readonly kind: 'dir' }
  | { readonly kind: 'symlink'; readonly target: string }
)

const encoder = new TextEncoder()
const BLOCK = 512
const padding = (size: number): Uint8Array => new Uint8Array((BLOCK - size % BLOCK) % BLOCK)
type TarBody = { readonly type: '0' | '5' | '2'; readonly size: number; readonly link?: string }

function paxRecord(key: string, value: string): string {
  const record = ` ${key}=${value}\n`
  const bytes = encoder.encode(record).length
  let length = bytes + 1
  while (String(length).length + bytes !== length) length = String(length).length + bytes
  return `${length}${record}`
}

function header(entry: { readonly name: string; readonly prefix?: string; readonly mode: number; readonly size: number; readonly type: string; readonly link?: string }): Uint8Array {
  const block = new Uint8Array(BLOCK)
  const text = (offset: number, value: string): void => { block.set(encoder.encode(value), offset) }
  const octal = (offset: number, width: number, value: number): void => { text(offset, value.toString(8).padStart(width - 1, '0') + '\0') }
  // CPython PAX_FORMAT keeps a truncated ASCII-replacement ustar field;
  // the PAX path/linkpath, when needed, carries the original UTF-8 value.
  const ascii = (value: string): string => Array.from(value, (char) => /[^\x00-\x7f]/.test(char) ? '?' : char).join('').slice(0, 100)
  text(0, ascii(entry.name))
  octal(100, 8, entry.mode & 0o7777)
  octal(108, 8, 0)
  octal(116, 8, 0)
  octal(124, 12, entry.size)
  octal(136, 12, 0)
  block.fill(32, 148, 156)
  text(156, entry.type)
  text(157, ascii(entry.link ?? ''))
  text(257, 'ustar\0')
  text(263, '00')
  text(345, entry.prefix ?? '')
  const checksum = block.reduce((sum, byte) => sum + byte, 0)
  text(148, checksum.toString(8).padStart(6, '0') + '\0 ')
  return block
}

function entryBody(entry: TarEntry): TarBody {
  switch (entry.kind) {
    case 'bytes': return { type: '0', size: entry.content.length }
    case 'file': return { type: '0', size: entry.size }
    case 'dir': return { type: '5', size: 0 }
    case 'symlink': return { type: '2', size: 0, link: entry.target }
    default: return assertNever(entry)
  }
}

function assertNever(value: never): never { throw badRequest(`unsupported build context entry ${String(value)}`) }

/** Write bounded chunks sequentially; neither a source file nor the tar is buffered. @internal */
export async function writeTar(output: FileHandle, entries: readonly TarEntry[]): Promise<void> {
  const fsSpecifier = 'node:fs/promises'
  const constantsSpecifier = 'node:fs'
  const fs: typeof import('node:fs/promises') = await import(/* @vite-ignore */ /* webpackIgnore: true */ fsSpecifier)
  const { constants }: typeof import('node:fs') = await import(/* @vite-ignore */ /* webpackIgnore: true */ constantsSpecifier)
  let total = 0
  const write = async (bytes: Uint8Array): Promise<void> => {
    total += bytes.length
    if (total > MAX_CONTEXT_BYTES) throw badRequest('build context exceeds the 1 GiB limit')
    let offset = 0
    while (offset < bytes.length) {
      const { bytesWritten } = await output.write(bytes, offset, bytes.length - offset)
      if (bytesWritten === 0) throw badRequest('could not write build context archive')
      offset += bytesWritten
    }
  }
  // Sort full names by UTF-8 bytes, not JS's UTF-16 ordering or directory DFS.
  const ordered = entries.map((entry) => entry.kind === 'dir' && !entry.name.endsWith('/') ? { ...entry, name: `${entry.name}/` } : entry)
    .map((entry) => ({ entry, encoded: encoder.encode(entry.name) }))
    .sort((left, right) => {
      for (let i = 0; i < Math.min(left.encoded.length, right.encoded.length); i++) {
        const difference = left.encoded[i] - right.encoded[i]
        if (difference !== 0) return difference
      }
      return left.encoded.length - right.encoded.length
    })
  for (const { entry } of ordered) {
    let input: FileHandle | undefined
    try {
      let body = entryBody(entry)
      if (entry.kind === 'file') {
        // O_NONBLOCK prevents a concurrent replacement with a FIFO from hanging open.
        try { input = await fs.open(entry.localPath, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK) }
        catch (error) {
          // Native filesystem errors can originate in another realm.
          if (typeof error === 'object' && error !== null && 'code' in error) {
            if (error.code === 'EACCES' || error.code === 'EPERM') {
              throw badRequest(`build context file is not readable: ${JSON.stringify(entry.localPath)}`)
            }
            if (['ELOOP', 'ENOENT', 'ENXIO', 'ENOTDIR'].includes(String(error.code))) {
              throw badRequest(`build context changed: ${JSON.stringify(entry.localPath)}`)
            }
          }
          throw error
        }
        const opened = await input.stat()
        if (!opened.isFile() || opened.dev !== entry.dev || opened.ino !== entry.ino) throw badRequest(`build context changed: ${JSON.stringify(entry.localPath)}`)
        body = { type: '0', size: opened.size }
      }
      if (!Number.isSafeInteger(body.size) || body.size < 0 || total + BLOCK + body.size + padding(body.size).length + 1024 > MAX_CONTEXT_BYTES) {
        throw badRequest('build context exceeds the 1 GiB limit')
      }
      const paxPath = entry.name.length > 100 || /[^\x00-\x7f]/.test(entry.name)
      const paxLink = body.link !== undefined && (body.link.length > 100 || /[^\x00-\x7f]/.test(body.link))
      if (paxPath || paxLink) {
        const content = encoder.encode((paxPath ? paxRecord('path', entry.name) : '') + (paxLink ? paxRecord('linkpath', body.link ?? '') : ''))
        await write(header({ name: '././@PaxHeader', mode: 0, size: content.length, type: 'x' }))
        await write(content)
        await write(padding(content.length))
      }
      await write(header({ ...body, name: entry.name, mode: entry.mode }))
      switch (entry.kind) {
        case 'bytes': await write(entry.content); break
        case 'file': {
          if (input === undefined) throw badRequest(`build context changed: ${JSON.stringify(entry.localPath)}`)
          const chunk = new Uint8Array(64 * 1024)
          for (let offset = 0; offset < body.size;) {
            const { bytesRead } = await input.read(chunk, 0, Math.min(chunk.length, body.size - offset), offset)
            if (bytesRead === 0) throw badRequest(`build context changed: ${JSON.stringify(entry.localPath)}`)
            await write(chunk.subarray(0, bytesRead))
            offset += bytesRead
          }
          break
        }
        case 'dir': case 'symlink': break
        default: assertNever(entry)
      }
      await write(padding(body.size))
    } finally { await input?.close() }
  }
  await write(new Uint8Array(1024))
  // tarfile's stream writer closes at a 20-block (10240-byte) record boundary.
  await write(new Uint8Array((10240 - total % 10240) % 10240))
}
