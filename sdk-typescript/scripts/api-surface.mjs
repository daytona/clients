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
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

// The package directory; the unit tests point it at a fixture package.
let root = join(dirname(fileURLToPath(import.meta.url)), '..')
const REGENERATE = 'npm run api-surface (in sdk-typescript/)'
const ENTRIES = [
  { spec: '@daytona/sdk', file: 'src/index.ts', out: 'api-surface/index.api.txt', publicConstructors: true },
  { spec: '@daytona/sdk/v2', file: 'src/v2/index.ts', out: 'api-surface/v2.api.txt' },
]

const BUILDER_FLAGS =
  ts.NodeBuilderFlags.NoTruncation |
  ts.NodeBuilderFlags.UseAliasDefinedOutsideCurrentScope |
  ts.NodeBuilderFlags.WriteClassExpressionAsTypeLiteral |
  ts.NodeBuilderFlags.UseSingleQuotesForStringLiteralType |
  ts.NodeBuilderFlags.IgnoreErrors

// Types are printed from the checker's type nodes with every union's members
// sorted: the checker orders them by internal type id, which depends on the
// order files are checked, so an unrelated change elsewhere would reorder them.
const printer = ts.createPrinter({ removeComments: true, omitTrailingSemicolon: true })
const printFile = ts.createSourceFile('surface.d.ts', '', ts.ScriptTarget.Latest)
const printNode = (node) => oneLine(printer.printNode(ts.EmitHint.Unspecified, node, printFile))
const LAST = new Set(['null', 'undefined'])

export function sortUnions(node) {
  const sorter = (context) => (top) => {
    const visit = (n) => {
      const updated = ts.visitEachChild(n, visit, context)
      if (!ts.isUnionTypeNode(updated)) return updated
      const keyed = updated.types.map((t) => [LAST.has(printNode(t)) ? 1 : 0, printNode(t), t])
      keyed.sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0))
      return context.factory.updateUnionTypeNode(updated, context.factory.createNodeArray(keyed.map((k) => k[2])))
    }
    return ts.visitNode(top, visit)
  }
  const result = ts.transform(node, [sorter])
  const [sorted] = result.transformed
  result.dispose()
  return sorted
}

// The printed text of a type or a signature, unions sorted.
export function printType(checker, type, at, extra = 0) {
  return printNode(sortUnions(checker.typeToTypeNode(type, at, BUILDER_FLAGS | extra)))
}

export function printSignature(checker, sig, at) {
  return printNode(sortUnions(checker.signatureToSignatureDeclaration(sig, ts.SyntaxKind.CallSignature, at, BUILDER_FLAGS)))
}

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

// Declarations expanded when a listed signature uses them. The v0 namespace
// is the bundled 0.x release's surface, versioned by that release; its
// unbundled stub is not it.
function isReachableSource(decl) {
  const rel = relative(root, decl.getSourceFile().fileName).split(sep).join('/')
  return isOwnSource(decl) && !rel.startsWith('src/v0')
}

function modifierFlags(decl) {
  return ts.canHaveModifiers(decl) || ts.isPropertySignature(decl) ? ts.getCombinedModifierFlags(decl) : 0
}

function typeParams(decl) {
  const params = decl.typeParameters
  return params && params.length ? `<${params.map((p) => oneLine(p.getText())).join(', ')}>` : ''
}

/**
 * Builds the surface of every entry point. Exported declarations are listed
 * under their exported name. A declaration of this package that a listed
 * signature uses but no entry point exports (an options type, the v2 client
 * reached through the root's `.v2`) is listed too, under `~Name`, in the
 * file of the first entry point that reaches it, so its members are covered.
 * @param {ts.TypeChecker} checker
 */
function surfaces(checker, program) {
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
  const exportedNames = new Set()
  const perEntry = ENTRIES.map((entry) => {
    const moduleSymbol = checker.getSymbolAtLocation(program.getSourceFile(join(root, entry.file)))
    const exportsByTarget = new Map()
    const exported = checker.getExportsOfModule(moduleSymbol).map((sym) => {
      const target = sym.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(sym) : sym
      const list = exportsByTarget.get(target) ?? []
      list.push(sym.name)
      exportsByTarget.set(target, list)
      exportedNames.add(sym.name)
      return { name: sym.name, target }
    })
    return { entry, exported, exportsByTarget, lines: [] }
  })
  // Every declaration listed under a name of its own: an exported one, or
  // one reached from a listed signature. Members declared on one are listed
  // under it only; a class or interface extending it lists only what it adds.
  const named = new Map()
  for (const { exportsByTarget } of perEntry) for (const target of exportsByTarget.keys()) named.set(target, target.name)
  const usedNames = new Set(exportedNames)
  // Types callers pass in: named in a parameter of a listed signature, or in
  // a property of such a type. A new required member breaks those callers,
  // so each gets a `Name: passed-in` line for the breaking-change check. A
  // type the SDK also returns (a return type, a property of a class or of a
  // returned type) is left out: callers hand back what they were given.
  let inputs = new Set()
  let outputs = new Set()
  const listedAt = new Map()
  // Methods of listed interfaces and object types: who implements them
  // depends on whether the type is passed in (callers implement its methods,
  // so the SDK supplies their arguments) or handed out (the SDK implements
  // them), which is known only once every signature is seen.
  const deferred = []

  // The v2 namespace first, so a declaration both reach is listed with v2.
  for (const state of [...perEntry].reverse()) emitEntry(state)
  const baseInputs = new Set(inputs)
  const baseOutputs = new Set(outputs)
  // Solved from the known directions up (a least fixed point, so no guess
  // can sustain itself): a container is caller-implemented once it is passed
  // in, SDK-implemented once it is handed out. A container reached neither
  // way (an exported interface no signature names) counts as the SDK's, and
  // only after the solution without it is stable.
  const containers = new Set(deferred.map((d) => d.container))
  const callerImplemented = new Set()
  const sdkImplemented = new Set()
  const solve = () => {
    inputs = new Set(baseInputs)
    outputs = new Set(baseOutputs)
    for (const { container, sig } of deferred) {
      // A type both passed in and handed out: its methods go both ways.
      if (callerImplemented.has(container)) flowSignature(sig, inputs)
      if (sdkImplemented.has(container)) flowSignature(sig, outputs)
    }
    for (const into of [inputs, outputs]) {
      const done = new Set()
      for (let pending = [...into]; pending.length; pending = [...into].filter((sym) => !done.has(sym))) {
        for (const sym of pending) {
          done.add(sym)
          collectInputs(checker.getDeclaredTypeOfSymbol(sym), new Set(), true, into)
        }
      }
    }
  }
  for (let changed = true; changed; ) {
    changed = false
    solve()
    const grow = (set, sym) => {
      if (set.has(sym)) return
      set.add(sym)
      changed = true
    }
    for (const sym of containers) {
      if (inputs.has(sym)) grow(callerImplemented, sym)
      if (outputs.has(sym)) grow(sdkImplemented, sym)
    }
    if (changed) continue
    for (const sym of containers) {
      if (!callerImplemented.has(sym) && !sdkImplemented.has(sym)) grow(sdkImplemented, sym)
    }
  }
  solve()
  // A base interface's members are listed under the base, so the base is
  // passed in whenever an interface extending it is, even if the SDK also
  // returns it: callers building the derived type supply its members.
  const passedIn = new Set([...inputs].filter((sym) => !outputs.has(sym)))
  for (const sym of passedIn) {
    if (!(sym.flags & ts.SymbolFlags.Interface)) continue
    const bases = new Set()
    for (const base of checker.getBaseTypes(checker.getDeclaredTypeOfSymbol(sym))) collectInputs(base, new Set(), false, bases)
    for (const base of bases) passedIn.add(base)
  }
  for (const sym of passedIn) {
    const at = listedAt.get(sym)
    // Only object types have members a caller supplies.
    const head = new RegExp(`^${at?.name.replace(/[.*+?^${}()|[\]\\/@~]/g, '\\$&')}: (interface|type (<.*> )?= \\{\\.\\.\\.\\})`)
    // `also-returned`: a base the SDK also returns; its members stay required
    // for readers, so the check never relaxes them.
    if (at && at.lines.some((line) => head.test(line))) {
      at.lines.push(`${at.name}: passed-in${outputs.has(sym) ? ' also-returned' : ''}`)
    }
  }
  // A type callers pass in that the SDK also returns (left out of
  // `passed-in` so it may gain required members): its members go both ways,
  // so the check compares them in both directions.
  for (const sym of inputs) {
    if (passedIn.has(sym) || !outputs.has(sym)) continue
    const at = listedAt.get(sym)
    const head = new RegExp(`^${at?.name.replace(/[.*+?^${}()|[\]\\/@~]/g, '\\$&')}: (interface|type (<.*> )?= \\{\\.\\.\\.\\})`)
    if (at && at.lines.some((line) => head.test(line))) at.lines.push(`${at.name}: returned also-passed-in`)
  }
  return perEntry.map(({ entry, lines }) => ({ entry, lines }))

  // A signature whose arguments flow into `into`'s opposite and whose return
  // value flows into `into`: a function callers implement when `into` is the
  // inputs (the SDK passes the arguments, the caller returns the value).
  function flowSignature(sig, into) {
    const other = into === inputs ? outputs : inputs
    // Whoever calls it chooses the type arguments.
    for (const tp of sig.typeParameters ?? []) collectInputs(tp, new Set(), false, other)
    for (const param of sig.parameters) {
      const decl = param.valueDeclaration
      if (decl) collectInputs(checker.getTypeOfSymbolAtLocation(param, decl), new Set(), false, other)
    }
    collectInputs(checker.getReturnTypeOfSignature(sig), new Set(), false, into)
  }

  // Records the named declarations of this package a type uses, in the
  // direction `into` (inputs: callers supply it; outputs: the SDK does),
  // through unions, intersections, type arguments and inline object types.
  // A function type or method met here is implemented by whoever supplies
  // the value: its parameters flow the other way, its return value this way.
  function collectInputs(type, seen = new Set(), top = false, into = inputs) {
    if (!type || seen.has(type)) return
    seen.add(type)
    if (type.flags & ts.TypeFlags.TypeParameter) {
      // `<T extends Opts>(o: T)`: callers pass an `Opts`.
      collectInputs(checker.getBaseConstraintOfType(type), seen, false, into)
      return
    }
    const record = (sym) => {
      if (sym.flags & ts.SymbolFlags.Alias) sym = checker.getAliasedSymbol(sym)
      if (sym.declarations?.length && sym.declarations.every(isReachableSource)) into.add(sym)
    }
    if (type.aliasSymbol && !top) {
      record(type.aliasSymbol)
      for (const arg of type.aliasTypeArguments ?? []) collectInputs(arg, seen, false, into)
      return
    }
    if (type.flags & (ts.TypeFlags.Union | ts.TypeFlags.Intersection)) {
      for (const member of type.types) collectInputs(member, seen, false, into)
      return
    }
    if (!(type.flags & ts.TypeFlags.Object)) return
    if (type.objectFlags & ts.ObjectFlags.Reference) {
      for (const arg of checker.getTypeArguments(type)) collectInputs(arg, seen, false, into)
    }
    const sym = type.symbol
    if (sym && sym.flags & (ts.SymbolFlags.Class | ts.SymbolFlags.Interface) && !top) {
      if (sym.flags & ts.SymbolFlags.Interface) record(sym)
      return
    }
    if (checker.isTupleType(type) || checker.isArrayType(type)) return
    const calls = checker.getSignaturesOfType(type, ts.SignatureKind.Call)
    for (const sig of calls) flowSignature(sig, into)
    if (calls.length && !checker.getPropertiesOfType(type).length) return
    for (const info of checker.getIndexInfosOfType(type)) collectInputs(info.type, seen, false, into)
    for (const prop of checker.getPropertiesOfType(type)) {
      const decl = prop.declarations?.[0]
      if (!decl) continue
      const propType = checker.getTypeOfSymbolAtLocation(prop, decl)
      if (prop.flags & ts.SymbolFlags.Method) {
        for (const sig of checker.getSignaturesOfType(propType, ts.SignatureKind.Call)) flowSignature(sig, into)
      } else {
        collectInputs(propType, seen, false, into)
      }
    }
  }

  function emitEntry({ entry, exported, exportsByTarget, lines }) {
    const queue = []
    const emit = (name, kind, signature = '') => lines.push(`${name}: ${kind}${signature ? ` ${oneLine(signature)}` : ''}`)
    const typeText = (type, at, extra = 0) => printType(checker, type, at, extra)
    const signatureText = (sig, at) => printSignature(checker, sig, at)

    function want(sym) {
      if (!sym) return
      if (sym.flags & ts.SymbolFlags.Alias) sym = checker.getAliasedSymbol(sym)
      if (named.has(sym) || !(sym.flags & (ts.SymbolFlags.Class | ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias | ts.SymbolFlags.Enum))) return
      const decls = sym.declarations ?? []
      if (!decls.length || !decls.every(isReachableSource) || decls.every(isInternal)) return
      let name = `~${sym.name}`
      if (usedNames.has(sym.name) || usedNames.has(name)) {
        const file = relative(join(root, 'src'), decls[0].getSourceFile().fileName).split(sep).join('/')
        name = `~${sym.name}@${file.replace(/\.ts$/, '')}`
      }
      usedNames.add(name)
      named.set(sym, name)
      queue.push(sym)
    }

    // Records the declarations a printed type refers to by name.
    function reach(type, seen = new Set(), top = false) {
      if (!type || seen.has(type)) return
      seen.add(type)
      if (type.aliasSymbol && !top) {
        want(type.aliasSymbol)
        for (const arg of type.aliasTypeArguments ?? []) reach(arg, seen)
        return
      }
      if (type.flags & (ts.TypeFlags.Union | ts.TypeFlags.Intersection)) {
        if (type.symbol && type.symbol.flags & ts.SymbolFlags.Enum) want(type.symbol)
        for (const member of type.types) reach(member, seen)
        return
      }
      if (type.flags & ts.TypeFlags.EnumLiteral && type.symbol) {
        want(type.symbol.flags & ts.SymbolFlags.EnumMember ? type.symbol.parent : type.symbol)
        return
      }
      if (!(type.flags & ts.TypeFlags.Object)) return
      if (type.objectFlags & ts.ObjectFlags.Reference) for (const arg of checker.getTypeArguments(type)) reach(arg, seen)
      const sym = type.symbol
      if (sym && sym.flags & (ts.SymbolFlags.Class | ts.SymbolFlags.Interface | ts.SymbolFlags.Enum) && !top) {
        want(sym)
        return
      }
      if (checker.isTupleType(type) || checker.isArrayType(type)) return
      for (const prop of checker.getPropertiesOfType(type)) {
        const decl = prop.declarations?.[0]
        if (decl) reach(checker.getTypeOfSymbolAtLocation(prop, decl), seen)
      }
      for (const kind of [ts.SignatureKind.Call, ts.SignatureKind.Construct]) {
        for (const sig of checker.getSignaturesOfType(type, kind)) reachSignature(sig, seen, false)
      }
      for (const info of checker.getIndexInfosOfType(type)) reach(info.type, seen)
    }

    function reachSignature(sig, seen = new Set(), passedIn = true) {
      for (const tp of sig.typeParameters ?? []) {
        reach(checker.getBaseConstraintOfType(tp), seen)
        // Callers choose each type argument, which must satisfy the
        // constraint, used elsewhere in the signature or not.
        if (passedIn) collectInputs(tp)
      }
      for (const param of sig.parameters) {
        const decl = param.valueDeclaration
        if (!decl) continue
        const type = checker.getTypeOfSymbolAtLocation(param, decl)
        reach(type, seen)
        if (passedIn) collectInputs(type)
      }
      reach(checker.getReturnTypeOfSignature(sig), seen)
      if (passedIn) collectInputs(checker.getReturnTypeOfSignature(sig), new Set(), false, outputs)
    }

    function ownedHere(member, container) {
      const decl = member.declarations?.[0]
      if (!decl || !isOwnSource(decl) || isInternal(decl)) return false
      const flags = modifierFlags(decl)
      if (flags & (ts.ModifierFlags.Private | ts.ModifierFlags.Protected)) return false
      if (decl.name && ts.isPrivateIdentifier(decl.name)) return false
      const owner = decl.parent && decl.parent.symbol
      if (!owner || owner === container) return true
      return !named.has(owner)
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
          if (member.flags & ts.SymbolFlags.Optional) mods.push('optional')
          for (const sig of checker.getSignaturesOfType(memberType, ts.SignatureKind.Call).filter(isPublicSignature)) {
            // A class's methods are the SDK's; an interface's or object
            // type's may be the caller's (decided once all are seen).
            if (container.flags & ts.SymbolFlags.Class) reachSignature(sig)
            else {
              reachSignature(sig, new Set(), false)
              deferred.push({ container, sig })
            }
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
        reach(shown)
        if (container.flags & ts.SymbolFlags.Class) {
          collectInputs(shown, new Set(), false, outputs)
          // A settable property: callers assign it too.
          if (!mods.includes('readonly')) collectInputs(shown)
        }
        emit(name, [...mods, 'property'].join(' '), typeText(shown, decl))
      }
      for (const kind of [ts.SignatureKind.Call, ts.SignatureKind.Construct]) {
        if (statics) continue
        for (const sig of checker.getSignaturesOfType(type, kind)) {
          if (!sig.declaration || !isOwnSource(sig.declaration) || isInternal(sig.declaration)) continue
          reachSignature(sig)
          emit(`${prefix}()`, kind === ts.SignatureKind.Call ? 'call' : 'new', signatureText(sig, sig.declaration))
        }
      }
      for (const info of checker.getIndexInfosOfType(type)) {
        if (statics || !info.declaration || !isOwnSource(info.declaration)) continue
        reach(info.type)
        emit(`${prefix}[]`, 'index', oneLine(info.declaration.getText()))
      }
    }

    function emitClass(name, sym, reached) {
      const decl = sym.declarations.find(ts.isClassDeclaration)
      const instance = checker.getDeclaredTypeOfSymbol(sym)
      const parts = [typeParams(decl)]
      const bases = checker.getBaseTypes(instance)
      for (const base of bases) reach(base)
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
      const publicCtor = isError || (entry.publicConstructors && !reached)
      const ctorKind = publicCtor ? 'constructor' : 'internal-constructor'
      if (ctorFlags & ts.ModifierFlags.Private) emit(`${name}.constructor`, `private ${ctorKind}`)
      else if (ctorFlags & ts.ModifierFlags.Protected) emit(`${name}.constructor`, `protected ${ctorKind}`)
      else if (!ctor || !isInternal(ctor)) {
        const staticType = checker.getTypeOfSymbolAtLocation(sym, decl)
        for (const sig of checker.getSignaturesOfType(staticType, ts.SignatureKind.Construct).filter(isPublicSignature)) {
          if (publicCtor) reachSignature(sig)
          emit(`${name}.constructor`, ctorKind, signatureText(sig, decl).replace(/: [^:]*$/, ''))
        }
      }
      emitMembers(name, instance, sym, decl)
      emitMembers(name, checker.getTypeOfSymbolAtLocation(sym, decl), sym, decl, true)
    }

    function emitInterface(name, sym) {
      const decl = sym.declarations.find(ts.isInterfaceDeclaration)
      const type = checker.getDeclaredTypeOfSymbol(sym)
      for (const base of checker.getBaseTypes(type)) reach(base)
      const ext = decl.heritageClauses?.flatMap((h) => h.types.map((t) => oneLine(t.getText()))) ?? []
      emit(name, 'interface', `${typeParams(decl)}${ext.length ? ` extends ${ext.join(', ')}` : ''}`)
      emitMembers(name, type, sym, decl)
    }

    function emitTypeAlias(name, sym) {
      const decl = sym.declarations.find(ts.isTypeAliasDeclaration)
      const type = checker.getTypeAtLocation(decl.type)
      if (ts.isTypeLiteralNode(decl.type) || (!decl.typeParameters && expandsToMembers(type))) {
        emit(name, 'type', `${typeParams(decl)} = {...}`)
        emitMembers(name, type, sym, decl)
        return
      }
      reach(type, new Set(), true)
      const text = typeText(type, decl, ts.NodeBuilderFlags.InTypeAlias)
      if (type.flags & ts.TypeFlags.Union && text.length > LONG) {
        emit(name, 'type', `${typeParams(decl)} = union`)
        for (const member of type.types) emit(name, 'union-member', typeText(member, decl))
        return
      }
      emit(name, 'type', `${typeParams(decl)} = ${text}`)
    }

    function emitValue(name, keyword, type, at) {
      reach(type)
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

    function emitSymbol(name, sym, reached = false) {
      const flags = sym.flags
      if (flags & ts.SymbolFlags.Class) emitClass(name, sym, reached)
      else if (flags & ts.SymbolFlags.Enum) emitEnum(name, sym)
      else if (flags & ts.SymbolFlags.Function) {
        const decl = sym.declarations[0]
        const signatures = checker.getSignaturesOfType(checker.getTypeOfSymbolAtLocation(sym, decl), ts.SignatureKind.Call)
        for (const sig of signatures.filter(isPublicSignature)) {
          reachSignature(sig)
          emit(name, 'function', signatureText(sig, decl))
        }
      } else if (flags & ts.SymbolFlags.Variable) {
        const decl = sym.declarations[0]
        const keyword = ts.getCombinedNodeFlags(decl) & ts.NodeFlags.Const ? 'const' : 'let'
        const valueType = checker.getTypeOfSymbolAtLocation(sym, decl)
        // A function value (`export const make = (o: Opts) => ...`) is the
        // SDK's: callers pass its arguments.
        for (const sig of checker.getSignaturesOfType(valueType, ts.SignatureKind.Call)) reachSignature(sig)
        emitValue(name, keyword, valueType, decl)
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
      if (flags & (ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias) && !(flags & ts.SymbolFlags.Class)) {
        listedAt.set(sym, { name, lines })
      }
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
    while (queue.length) {
      const sym = queue.shift()
      emitSymbol(named.get(sym), sym, true)
    }
  }
}

// A long union or tuple prints one member per line, so adding or removing
// one is a one-line change.
const LONG = 120

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

/** The rendered snapshot text of each entry point of the package in `dir`. */
export function generate(dir) {
  root = dir
  const program = createProgram()
  const checker = program.getTypeChecker()
  return Object.fromEntries(surfaces(checker, program).map(({ entry, lines }) => [entry.out, render(entry, lines)]))
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
  for (const { entry, lines } of surfaces(checker, program)) {
    const text = render(entry, lines)
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

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  process.exitCode = main(process.argv.slice(2))
}
