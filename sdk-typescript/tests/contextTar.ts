/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

export interface TarEntry {
  readonly name: string
  readonly contents: Buffer
  readonly type: string
  readonly mode: number
  readonly link: string
  readonly mtime: number
  readonly uid: number
  readonly gid: number
  readonly uname: string
  readonly gname: string
}

interface PaxAttributes { readonly [key: string]: string }

/** Read the ustar headers independently of the SDK's archive writer. */
export function parseTar(bytes: Uint8Array): readonly TarEntry[] {
  const tar = Buffer.from(bytes)
  const entries: TarEntry[] = []
  let attributes: PaxAttributes = {}
  for (let offset = 0; offset + 512 <= tar.length;) {
    const header = tar.subarray(offset, offset + 512)
    if (header.every((byte) => byte === 0)) break
    const text = (start: number, length: number): string => header.subarray(start, start + length).toString('utf8').split('\0')[0]
    const octal = (start: number, length: number): number => Number.parseInt(text(start, length).trim() || '0', 8)
    const size = octal(124, 12)
    const content = tar.subarray(offset + 512, offset + 512 + size)
    if (content.length !== size) throw new TypeError('truncated tar entry')
    const type = text(156, 1) || '0'
    const prefix = text(345, 155)
    const name = prefix ? `${prefix}/${text(0, 100)}` : text(0, 100)
    if (type === 'x') {
      const pax: Record<string, string> = {}
      for (let position = 0; position < content.length;) {
        const space = content.indexOf(32, position)
        const length = Number(content.subarray(position, space).toString())
        if (space < position || !Number.isInteger(length) || length <= 0) throw new TypeError('invalid pax record')
        const record = content.subarray(space + 1, position + length - 1).toString()
        const equals = record.indexOf('=')
        if (equals < 0) throw new TypeError('invalid pax attribute')
        pax[record.slice(0, equals)] = record.slice(equals + 1)
        position += length
      }
      attributes = pax
    } else {
      entries.push({
        name: attributes.path ?? name, contents: content, type, mode: octal(100, 8),
        link: attributes.linkpath ?? text(157, 100), mtime: octal(136, 12),
        uid: octal(108, 8), gid: octal(116, 8), uname: text(265, 32), gname: text(297, 32),
      })
      attributes = {}
    }
    offset += 512 + Math.ceil(size / 512) * 512
  }
  return entries
}
