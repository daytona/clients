/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Writes the public declaration surface of the package entry points to
// api-surface/*.api.txt, one declaration per line, sorted: every exported
// name with its kind and full type signature, plus the members of exported
// classes, interfaces, object type aliases, enums and namespaces.
//
//   npm run api-surface          regenerate the committed files
//   npm run api-surface:check    fail when a committed file is stale
//
// Line format, shared with the Python and CLI snapshots and read by
// scripts/breaking/breaking_changes.py: `<name>: <kind>[ <signature>]`.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const REGENERATE = 'npm run api-surface (in sdk-typescript/)'
const ENTRIES = [
  { spec: '@daytona/sdk', file: 'src/index.ts', out: 'api-surface/index.api.txt', publicConstructors: true },
  { spec: '@daytona/sdk/v2', file: 'src/v2/index.ts', out: 'api-surface/v2.api.txt' },
]

const TYPE_FLAGS =
  ts.TypeFormatFlags.NoTruncation |
  ts.TypeFormatFlags.UseAliasDefinedOutsideCurrentScope |
  ts.TypeFormatFlags.WriteClassExpressionAsTypeLiteral |
  ts.TypeFormatFlags.UseSingleQuotesForStringLiteralType

function createProgram() {
  const configPath = join(root, 'tsconfig.json')
  const config = ts.readConfigFile(configPath, ts.sys.readFile)
  if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'))
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
  // The v0 types always come from the unbundled stub, so the output does not
  // depend on whether this checkout has built or bundled v0.
  const paths = { ...parsed.options.paths, '@daytona/sdk/v0': ['./src/v0-unbundled.ts'] }
  const options = { ...parsed.options, paths, noEmit: true }
  return ts.createProgram(
    ENTRIES.map((e) => join(root, e.file)),
    options,
  )
}

const oneLine = (text) => text.replace(/\s+/g, ' ').trim()

function isInternal(decl) {
  return Boolean(decl) && ts.getJSDocTags(decl).some((tag) => tag.tagName.text === 'internal')
}

// stripInternal drops an `@internal` overload from the published
// declarations; so does the snapshot.
const isPublicSignature = (sig) => !isInternal(sig.declaration)

function isOwnSource(decl) {
  const file = decl.getSourceFile().fileName
  const rel = relative(root, file).split(sep).join('/')
  return rel.startsWith('src/') && !rel.includes('node_modules')
}

function modifierFlags(decl) {
  return ts.canHaveModifiers(decl) || ts.isPropertySignature(decl) ? ts.getCombinedModifierFlags(decl) : 0
}

function typeParams(decl) {
  const params = decl.typeParameters
  return params && params.length ? `<${params.map((p) => oneLine(p.getText())).join(', ')}>` : ''
}

/** @param {ts.TypeChecker} checker */
function surfaceOf(checker, sourceFile, entry) {
  const lines = []
  const moduleSymbol = checker.getSymbolAtLocation(sourceFile)
  const exportsByTarget = new Map()
  const exported = checker.getExportsOfModule(moduleSymbol).map((sym) => {
    const target = sym.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(sym) : sym
    const list = exportsByTarget.get(target) ?? []
    list.push(sym.name)
    exportsByTarget.set(target, list)
    return { name: sym.name, target }
  })
  // Members declared on an exported class or interface are listed under it;
  // a class extending it lists only what it adds.
  const exportedContainers = new Set(exportsByTarget.keys())

  const emit = (name, kind, signature = '') => lines.push(`${name}: ${kind}${signature ? ` ${oneLine(signature)}` : ''}`)
  const typeText = (type, at) => checker.typeToString(type, at, TYPE_FLAGS)
  const signatureText = (sig, at) => checker.signatureToString(sig, at, TYPE_FLAGS)

  function ownedHere(member, container) {
    const decl = member.declarations?.[0]
    if (!decl || !isOwnSource(decl) || isInternal(decl)) return false
    const flags = modifierFlags(decl)
    if (flags & (ts.ModifierFlags.Private | ts.ModifierFlags.Protected)) return false
    if (decl.name && ts.isPrivateIdentifier(decl.name)) return false
    const owner = decl.parent && decl.parent.symbol
    if (!owner || owner === container) return true
    return !exportedContainers.has(owner)
  }

  function memberName(prefix, member) {
    const decl = member.declarations?.[0]
    if (decl?.name && ts.isComputedPropertyName(decl.name)) return `${prefix}[${oneLine(decl.name.expression.getText())}]`
    return `${prefix}.${member.name}`
  }

  function emitMembers(prefix, type, container, at, statics = false) {
    const members = checker.getPropertiesOfType(type).filter((m) => m.name !== 'prototype' && ownedHere(m, container))
    for (const member of members) {
      const decl = member.declarations[0]
      const name = memberName(prefix, member)
      const memberType = checker.getTypeOfSymbolAtLocation(member, decl)
      const flags = modifierFlags(decl)
      const mods = [statics ? 'static' : '', flags & ts.ModifierFlags.Abstract ? 'abstract' : ''].filter(Boolean)
      const isMethod = member.flags & ts.SymbolFlags.Method
      if (isMethod) {
        for (const sig of checker.getSignaturesOfType(memberType, ts.SignatureKind.Call).filter(isPublicSignature)) {
          emit(name, [...mods, 'method'].join(' '), signatureText(sig, decl))
        }
        continue
      }
      const getterOnly =
        member.flags & ts.SymbolFlags.GetAccessor && !(member.flags & ts.SymbolFlags.SetAccessor)
      if (flags & ts.ModifierFlags.Readonly || getterOnly) mods.push('readonly')
      let shown = memberType
      if (member.flags & ts.SymbolFlags.Optional) {
        mods.push('optional')
        // `x?: T` reads as T; the checker adds `| undefined` on its own.
        if (decl.type) shown = checker.getTypeFromTypeNode(decl.type)
      }
      emit(name, [...mods, 'property'].join(' '), typeText(shown, decl))
    }
    for (const kind of [ts.SignatureKind.Call, ts.SignatureKind.Construct]) {
      if (statics) continue
      for (const sig of checker.getSignaturesOfType(type, kind)) {
        if (!sig.declaration || !isOwnSource(sig.declaration) || isInternal(sig.declaration)) continue
        emit(`${prefix}()`, kind === ts.SignatureKind.Call ? 'call' : 'new', signatureText(sig, sig.declaration))
      }
    }
    for (const info of checker.getIndexInfosOfType(type)) {
      if (statics || !info.declaration || !isOwnSource(info.declaration)) continue
      emit(`${prefix}[]`, 'index', oneLine(info.declaration.getText()))
    }
  }

  function emitClass(name, sym) {
    const decl = sym.declarations.find(ts.isClassDeclaration)
    const instance = checker.getDeclaredTypeOfSymbol(sym)
    const parts = [typeParams(decl)]
    const bases = checker.getBaseTypes(instance)
    if (bases.length) parts.push(` extends ${bases.map((b) => typeText(b, decl)).join(', ')}`)
    const impl = decl.heritageClauses?.find((h) => h.token === ts.SyntaxKind.ImplementsKeyword)
    if (impl) parts.push(` implements ${impl.types.map((t) => oneLine(t.getText())).join(', ')}`)
    const abstract = modifierFlags(decl) & ts.ModifierFlags.Abstract
    emit(name, abstract ? 'abstract class' : 'class', parts.join(''))
    const ctor = decl.members.find(ts.isConstructorDeclaration)
    const ctorFlags = ctor ? modifierFlags(ctor) : 0
    // Constructors of exported classes are not public API, except where the
    // caller builds the instance itself: the root client, the error classes.
    const isError = [instance, ...bases].some((t) => checker.getPropertiesOfType(t).some((p) => p.name === 'stack'))
    const ctorKind = isError || entry.publicConstructors ? 'constructor' : 'internal-constructor'
    if (ctorFlags & ts.ModifierFlags.Private) emit(`${name}.constructor`, `private ${ctorKind}`)
    else if (ctorFlags & ts.ModifierFlags.Protected) emit(`${name}.constructor`, `protected ${ctorKind}`)
    else if (!ctor || !isInternal(ctor)) {
      const staticType = checker.getTypeOfSymbolAtLocation(sym, decl)
      for (const sig of checker.getSignaturesOfType(staticType, ts.SignatureKind.Construct).filter(isPublicSignature)) {
        emit(`${name}.constructor`, ctorKind, signatureText(sig, decl).replace(/: [^:]*$/, ''))
      }
    }
    emitMembers(name, instance, sym, decl)
    emitMembers(name, checker.getTypeOfSymbolAtLocation(sym, decl), sym, decl, true)
  }

  function emitInterface(name, sym) {
    const decl = sym.declarations.find(ts.isInterfaceDeclaration)
    const type = checker.getDeclaredTypeOfSymbol(sym)
    const ext = decl.heritageClauses?.flatMap((h) => h.types.map((t) => oneLine(t.getText()))) ?? []
    emit(name, 'interface', `${typeParams(decl)}${ext.length ? ` extends ${ext.join(', ')}` : ''}`)
    emitMembers(name, type, sym, decl)
  }

  // A long union or tuple prints one member per line, so adding or removing
  // one is a one-line change.
  const LONG = 120

  function expandsToMembers(type) {
    return (
      type.flags & ts.TypeFlags.Object &&
      !(type.flags & ts.TypeFlags.Union) &&
      !checker.getSignaturesOfType(type, ts.SignatureKind.Call).length &&
      !checker.getSignaturesOfType(type, ts.SignatureKind.Construct).length &&
      !(type.symbol && type.symbol.flags & (ts.SymbolFlags.Class | ts.SymbolFlags.Interface)) &&
      !checker.isTupleType(type) &&
      !checker.isArrayType(type)
    )
  }

  function emitTypeAlias(name, sym) {
    const decl = sym.declarations.find(ts.isTypeAliasDeclaration)
    const type = checker.getTypeAtLocation(decl.type)
    if (ts.isTypeLiteralNode(decl.type) || (!decl.typeParameters && expandsToMembers(type))) {
      emit(name, 'type', `${typeParams(decl)} = {...}`)
      emitMembers(name, type, sym, decl)
      return
    }
    const text = checker.typeToString(type, decl, TYPE_FLAGS | ts.TypeFormatFlags.InTypeAlias)
    if (type.flags & ts.TypeFlags.Union && text.length > LONG) {
      emit(name, 'type', `${typeParams(decl)} = union`)
      for (const member of type.types) emit(name, 'union-member', typeText(member, decl))
      return
    }
    emit(name, 'type', `${typeParams(decl)} = ${text}`)
  }

  function emitValue(name, keyword, type, at) {
    const text = typeText(type, at)
    if (text.length > LONG && checker.isTupleType(type)) {
      const readonly = type.target?.readonly ? 'readonly ' : ''
      emit(name, keyword, `${readonly}tuple`)
      for (const element of checker.getTypeArguments(type)) emit(name, 'tuple-element', typeText(element, at))
      return
    }
    emit(name, keyword, text)
  }

  function emitEnum(name, sym) {
    emit(name, sym.flags & ts.SymbolFlags.ConstEnum ? 'const enum' : 'enum')
    for (const member of checker.getExportsOfModule(sym)) {
      const value = checker.getConstantValue(member.declarations[0])
      emit(`${name}.${member.name}`, 'enum-member', `= ${typeof value === 'string' ? `'${value}'` : String(value)}`)
    }
  }

  function emitSymbol(name, sym) {
    const flags = sym.flags
    if (flags & ts.SymbolFlags.Class) emitClass(name, sym)
    else if (flags & ts.SymbolFlags.Enum) emitEnum(name, sym)
    else if (flags & ts.SymbolFlags.Function) {
      const decl = sym.declarations[0]
      const signatures = checker.getSignaturesOfType(checker.getTypeOfSymbolAtLocation(sym, decl), ts.SignatureKind.Call)
      for (const sig of signatures.filter(isPublicSignature)) {
        emit(name, 'function', signatureText(sig, decl))
      }
    } else if (flags & ts.SymbolFlags.Variable) {
      const decl = sym.declarations[0]
      const keyword = ts.getCombinedNodeFlags(decl) & ts.NodeFlags.Const ? 'const' : 'let'
      emitValue(name, keyword, checker.getTypeOfSymbolAtLocation(sym, decl), decl)
    } else if (flags & ts.SymbolFlags.ValueModule) {
      emit(name, 'namespace')
      for (const member of checker.getExportsOfModule(sym)) {
        const target = member.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(member) : member
        emitSymbol(`${name}.${member.name}`, target)
      }
    }
    // A name can be both a value and a type (a const and a type alias).
    if (flags & ts.SymbolFlags.Interface && !(flags & ts.SymbolFlags.Class)) emitInterface(name, sym)
    if (flags & ts.SymbolFlags.TypeAlias) emitTypeAlias(name, sym)
  }

  for (const { name, target } of exported) {
    // An `@internal` declaration is not in the published declarations.
    if (target.declarations?.length && target.declarations.every(isInternal)) continue
    const names = exportsByTarget.get(target)
    // One name per declaration carries the members; the others point at it.
    const canonical = names.includes(target.name) ? target.name : [...names].sort()[0]
    if (name !== canonical) {
      emit(name, 'alias', `= ${canonical}`)
      continue
    }
    emitSymbol(name, target)
  }
  return lines
}

function sortKey(line) {
  const at = line.indexOf(': ')
  return [line.slice(0, at), line.slice(at + 2)]
}

function render(entry, lines) {
  const sorted = [...lines].sort((a, b) => {
    const [ka, ra] = sortKey(a)
    const [kb, rb] = sortKey(b)
    return ka < kb ? -1 : ka > kb ? 1 : ra < rb ? -1 : ra > rb ? 1 : 0
  })
  const header = [
    `# Public declaration surface of ${entry.spec} (${entry.file}).`,
    `# Generated by scripts/api-surface.mjs; do not edit. Regenerate: ${REGENERATE}.`,
  ]
  return `${[...header, ...sorted].join('\n')}\n`
}

function main(argv) {
  const check = argv.includes('--check')
  const program = createProgram()
  const diagnostics = ts.getPreEmitDiagnostics(program)
  if (diagnostics.length) {
    const host = { getCanonicalFileName: (f) => f, getCurrentDirectory: () => root, getNewLine: () => '\n' }
    process.stderr.write(ts.formatDiagnostics(diagnostics, host))
    return 1
  }
  const checker = program.getTypeChecker()
  let stale = 0
  for (const entry of ENTRIES) {
    const text = render(entry, surfaceOf(checker, program.getSourceFile(join(root, entry.file)), entry))
    const target = join(root, entry.out)
    let current = ''
    try {
      current = readFileSync(target, 'utf8')
    } catch {}
    if (current === text) continue
    if (check) {
      stale += 1
      process.stderr.write(`sdk-typescript/${entry.out} is stale: the public surface of ${entry.spec} changed.\n`)
      const before = new Set(current.split('\n'))
      const after = new Set(text.split('\n'))
      for (const line of current.split('\n')) if (line && !after.has(line)) process.stderr.write(`  - ${line}\n`)
      for (const line of text.split('\n')) if (line && !before.has(line)) process.stderr.write(`  + ${line}\n`)
      continue
    }
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, text)
    process.stdout.write(`wrote ${entry.out}\n`)
  }
  if (stale) {
    process.stderr.write(`Regenerate with \`${REGENERATE}\` and commit the result.\n`)
    return 1
  }
  return 0
}

process.exitCode = main(process.argv.slice(2))
