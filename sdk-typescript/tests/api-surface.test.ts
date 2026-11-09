/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// The committed surface snapshots (scripts/api-surface.mjs) must cover what
// users reach without importing it by name: the v2 client behind the root's
// `.v2`, and the options types public signatures use without exporting them.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as ts from 'typescript'

const root = join(__dirname, '..')
const snapshot = (file: string): string[] => readFileSync(join(root, 'api-surface', file), 'utf8').split('\n')

function declaration(file: string, name: string): ts.ClassDeclaration | ts.TypeAliasDeclaration | ts.InterfaceDeclaration {
  const source = ts.createSourceFile(file, readFileSync(join(root, file), 'utf8'), ts.ScriptTarget.Latest, true)
  const found = source.statements.find(
    (s): s is ts.ClassDeclaration | ts.TypeAliasDeclaration | ts.InterfaceDeclaration =>
      (ts.isClassDeclaration(s) || ts.isTypeAliasDeclaration(s) || ts.isInterfaceDeclaration(s)) && s.name?.text === name,
  )
  if (!found) throw new Error(`${name} not declared in ${file}`)
  return found
}

function isPublicMember(member: ts.ClassElement): boolean {
  if (!member.name || ts.isPrivateIdentifier(member.name) || ts.isConstructorDeclaration(member)) return false
  if (ts.getJSDocTags(member).some((tag) => tag.tagName.text === 'internal')) return false
  const modifiers = ts.canHaveModifiers(member) ? (ts.getModifiers(member) ?? []) : []
  return !modifiers.some((m) => m.kind === ts.SyntaxKind.PrivateKeyword || m.kind === ts.SyntaxKind.ProtectedKeyword)
}

describe('public surface snapshot', () => {
  it('lists every public member of the v2 client reached through the root', () => {
    const lines = snapshot('index.api.txt')
    expect(lines).toContain('Daytona.v2: readonly property DaytonaV2')
    const client = declaration('src/v2/Daytona.ts', 'Daytona')
    if (!ts.isClassDeclaration(client)) throw new Error('the v2 client is a class')
    const names = client.members.filter(isPublicMember).map((m) => (m.name as ts.Identifier).text)
    expect(names).toEqual(expect.arrayContaining(['create', 'get', 'list', 'findByName', 'getLimits', 'images', 'snapshots']))
    const missing = names.filter((name) => !lines.some((line) => line.startsWith(`~Daytona@v2/Daytona.${name}: `)))
    expect(missing).toEqual([])
    expect(lines.some((line) => line.startsWith('~Daytona@v2/Daytona.constructor: internal-constructor '))).toBe(true)
  })

  it('prints union members in a canonical order, independent of the order files are checked', () => {
    // The checker orders union members by internal type id; the generator
    // sorts them, so an unrelated change cannot reorder an untouched line.
    const literalUnion = /'[^']*'(?: \| '[^']*')+/g
    const unsorted: string[] = []
    for (const file of ['index.api.txt', 'v2.api.txt']) {
      for (const line of snapshot(file)) {
        for (const match of line.match(literalUnion) ?? []) {
          const members = match.split(' | ')
          if (members.join() !== [...members].sort().join()) unsorted.push(line)
        }
      }
    }
    expect(unsorted).toEqual([])
    expect(snapshot('v2.api.txt')).toContain("ComputerUseMouseButton: type = 'left' | 'middle' | 'right'")
  })

  it('marks the object types callers pass in, and only those', () => {
    const lines = snapshot('v2.api.txt')
    // Base interfaces of passed-in options too: their members are listed under the base.
    for (const name of ['PortMapping', 'CreateSandboxOptions', 'CreateRequestBaseOptions', 'SandboxSearchFilters', 'VolumeMount', '~LifecycleWaitOptions', '~ErrorEnvelope', 'OtelApiLike']) {
      expect(lines).toContain(`${name}: passed-in`)
    }
    // A base the SDK also returns (mouse calls return a point): marked, but
    // its members must stay required for readers.
    expect(lines).toContain('ComputerUsePoint: passed-in also-returned')
    // Returned to the caller, never passed in: may gain required members.
    expect(lines).not.toContain('OrganizationLimits: passed-in')
  })

  it('expands an options type that a public signature uses without exporting it', () => {
    const lines = snapshot('v2.api.txt')
    expect(lines.some((line) => line.startsWith('Sandbox.pause: method (options?: LifecycleWaitOptions)'))).toBe(true)
    const options = declaration('src/v2/Sandbox.ts', 'LifecycleWaitOptions')
    if (!ts.isTypeAliasDeclaration(options) || !ts.isTypeLiteralNode(options.type)) throw new Error('LifecycleWaitOptions is an object type')
    const members = options.type.members.map((m) => (m.name as ts.Identifier).text)
    expect(members.length).toBeGreaterThan(0)
    for (const member of members) {
      expect(lines.some((line) => line.startsWith(`~LifecycleWaitOptions.${member}: `))).toBe(true)
    }
  })
})
