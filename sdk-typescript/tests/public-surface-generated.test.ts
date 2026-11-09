/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// No public export, and no type in a public signature, comes from generated
// code. The wire types under src/v2/gen (the generated output and its alias
// layers) type the HTTP boundary only; the public surface is hand-written.
// Every type reachable from the package entry points (exported, or used by a
// public member of a reachable type) is walked with the compiler's type
// checker, and each generated type it reaches is a violation. ALLOWED lists the
// violations that remain; an entry that no longer occurs fails too, so the list
// only shrinks. sdk-python/tests/test_public_surface_generated.py holds the
// Python surface to the same rule.

import { join, sep } from 'node:path'
import ts from 'typescript'

// Today's violations, as "<owner>.<member>: <generated type>" or
// "export <name>". Remove an entry when its type becomes SDK-owned.
const ALLOWED: ReadonlySet<string> = new Set([
  // Client, sandbox lifecycle and platform services.
  'Daytona.currentApiKey: ProjectApiKeyWithContext',
  'Daytona.getLimits: OrganizationLimits',
  'Daytona.getProjectBudget: ProjectBudget',
  'Daytona.listAuditActions: AuditActionList',
  'Image.constructor(image): ImageListItem',
  'ImageBuilder.toRequestSource: CreateImageRequest',
  'MacModelsService.catalog: MacCatalog',
  'MacModelsService.list: MacModel',
  'ReservationsService.createBulk: MacReservationBatch',
  'SandboxDto.image: ImageReference',
  'SandboxDto.snapshot: SnapshotReference',
  'SecretService.get: Secret',
  'SecretService.iter: Secret',
  'SecretService.list: Secret',
  'SecretService.set: Secret',
  'Snapshot.constructor(snapshot): Snapshot',
  'export AuditActionGroup',
  'export AuditActionList',
  'export MacCatalog',
  'export MacModel',
  'export MacReservationBatch',
  'export OrganizationLimits',
  'export PortInUseResponse',
  'export PortListResponse',
  'export ProjectApiKeyWithContext',
  'export ProjectBudget',
  'export SandboxStateSnapshot',
  'export Secret',
  'export SnapshotDetail',
])

const root = join(__dirname, '..')
const genDir = join(root, 'src', 'v2', 'gen') + sep
const srcDir = join(root, 'src') + sep
const NAMED = ts.SymbolFlags.Class | ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias | ts.SymbolFlags.Enum

function generatedOnThePublicSurface(): Set<string> {
  const config = ts.readConfigFile(join(root, 'tsconfig.json'), ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
  const entries = [join(root, 'src', 'index.ts'), join(root, 'src', 'v2', 'index.ts')]
  const program = ts.createProgram(entries, { ...parsed.options, noEmit: true })
  const checker = program.getTypeChecker()

  const found = new Set<string>()
  const queue: { readonly owner: string; readonly symbol: ts.Symbol }[] = []
  const walkedSymbols = new Set<ts.Symbol>()
  const walkedTypes = new Set<ts.Type>()

  const inGen = (node: ts.Node): boolean => node.getSourceFile().fileName.startsWith(genDir)
  const inSdk = (node: ts.Node): boolean => node.getSourceFile().fileName.startsWith(srcDir) && !inGen(node)
  const resolve = (symbol: ts.Symbol): ts.Symbol => symbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol
  const sdkDeclared = (symbol: ts.Symbol): boolean => (symbol.declarations ?? []).some(inSdk)

  /** A readable name for an anonymous generated declaration: its nearest named ancestor. */
  function displayName(symbol: ts.Symbol, declaration: ts.Node): string {
    if (symbol.getName() !== '__type' && symbol.getName() !== '__object') return symbol.getName()
    for (let node: ts.Node | undefined = declaration.parent; node !== undefined; node = node.parent) {
      if ((ts.isPropertySignature(node) || ts.isTypeAliasDeclaration(node) || ts.isInterfaceDeclaration(node)) && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name))) {
        return node.name.text
      }
    }
    return symbol.getName()
  }

  /** The generated type a type annotation names (directly or through an SDK alias of one). */
  function nodeGenerated(node: ts.Node, depth = 0): string | undefined {
    if (depth > 12) return undefined
    if (ts.isTypeReferenceNode(node)) {
      const named = symbolGenerated(checker.getSymbolAtLocation(node.typeName), depth + 1)
      if (named !== undefined) return named
    }
    if (ts.isExpressionWithTypeArguments(node)) {
      const named = symbolGenerated(checker.getSymbolAtLocation(node.expression), depth + 1)
      if (named !== undefined) return named
    }
    let inner: string | undefined
    ts.forEachChild(node, (child) => {
      inner ??= nodeGenerated(child, depth + 1)
    })
    return inner
  }

  /** The generated type `symbol` is, or is just another name for. */
  function symbolGenerated(symbol: ts.Symbol | undefined, depth = 0): string | undefined {
    if (symbol === undefined || depth > 12) return undefined
    const target = resolve(symbol)
    for (const declaration of target.declarations ?? []) {
      if (inGen(declaration)) return displayName(target, declaration)
      if (!inSdk(declaration)) continue
      if (ts.isTypeAliasDeclaration(declaration)) {
        const inner = aliasOf(declaration.type, depth)
        if (inner !== undefined) return inner
      }
      if (ts.isInterfaceDeclaration(declaration) || ts.isClassDeclaration(declaration)) {
        for (const clause of declaration.heritageClauses ?? []) {
          for (const heritage of clause.types) {
            const inner = symbolGenerated(checker.getSymbolAtLocation(heritage.expression), depth + 1)
            if (inner !== undefined) return inner
          }
        }
      }
    }
    return undefined
  }

  /** An SDK alias is generated when it only renames (or wraps) a generated type. */
  function aliasOf(node: ts.TypeNode, depth: number): string | undefined {
    while (ts.isParenthesizedTypeNode(node) || ts.isTypeOperatorNode(node)) node = node.type
    if (ts.isTypeReferenceNode(node)) {
      const named = symbolGenerated(checker.getSymbolAtLocation(node.typeName), depth + 1)
      if (named !== undefined) return named
      for (const argument of node.typeArguments ?? []) {
        const inner = aliasOf(argument, depth + 1)
        if (inner !== undefined) return inner
      }
    }
    if (ts.isIndexedAccessTypeNode(node)) return aliasOf(node.objectType, depth + 1)
    if (ts.isArrayTypeNode(node)) return aliasOf(node.elementType, depth + 1)
    if (ts.isIntersectionTypeNode(node)) {
      for (const part of node.types) {
        const inner = aliasOf(part, depth + 1)
        if (inner !== undefined) return inner
      }
    }
    return undefined
  }

  function isPublic(declaration: ts.Declaration): boolean {
    if (ts.getCombinedModifierFlags(declaration) & (ts.ModifierFlags.Private | ts.ModifierFlags.Protected)) return false
    const name = (declaration as { readonly name?: ts.Node }).name
    if (name !== undefined && ts.isPrivateIdentifier(name)) return false
    return !(ts.getJSDocTags(declaration).some((tag) => tag.tagName.text === 'internal'))
  }

  function record(position: string, name: string): void {
    found.add(`${position}: ${name}`)
  }

  /** A type used at `position` (an annotation, when there is one, names it best). */
  function visit(position: string, type: ts.Type, annotation: ts.Node | undefined, depth: number): void {
    const named = annotation === undefined ? undefined : nodeGenerated(annotation)
    if (named !== undefined) {
      record(position, named)
      return
    }
    visitType(position, type, depth)
  }

  function visitType(position: string, type: ts.Type, depth: number): void {
    if (depth > 8) return
    const alias = type.aliasSymbol
    const symbol = type.getSymbol()
    const generated = symbolGenerated(alias) ?? symbolGenerated(symbol)
    if (generated !== undefined) {
      record(position, generated)
      return
    }
    if (type.isUnionOrIntersection()) {
      for (const part of type.types) visitType(position, part, depth + 1)
      return
    }
    for (const argument of type.aliasTypeArguments ?? []) visitType(position, argument, depth + 1)
    if (type.flags & ts.TypeFlags.Object && (type as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference) {
      for (const argument of checker.getTypeArguments(type as ts.TypeReference)) visitType(position, argument, depth + 1)
    }
    const named = alias ?? symbol
    if (named !== undefined && resolve(named).flags & NAMED) {
      const target = resolve(named)
      if (sdkDeclared(target) && !walkedSymbols.has(target)) queue.push({ owner: target.getName(), symbol: target })
      return
    }
    if (type.flags & ts.TypeFlags.Object && symbol !== undefined && sdkDeclared(symbol) && !walkedTypes.has(type)) {
      walkedTypes.add(type)
      walkStructure(position, type, depth + 1)
    }
  }

  function visitSignature(position: string, signature: ts.Signature, depth: number): void {
    for (const parameter of signature.getParameters()) {
      const declaration = parameter.valueDeclaration ?? parameter.declarations?.[0]
      if (declaration === undefined) continue
      const annotation = ts.isParameter(declaration) ? declaration.type : undefined
      visit(`${position}(${parameter.getName()})`, checker.getTypeOfSymbolAtLocation(parameter, declaration), annotation, depth + 1)
    }
    const declaration = signature.getDeclaration() as ts.SignatureDeclaration | undefined
    visit(position, signature.getReturnType(), declaration?.type, depth + 1)
  }

  function walkStructure(owner: string, type: ts.Type, depth: number): void {
    for (const property of type.getProperties()) {
      const declaration = property.valueDeclaration ?? property.declarations?.[0]
      if (declaration === undefined || !isPublic(declaration) || !inSdk(declaration)) continue
      const position = `${owner}.${property.getName()}`
      const propertyType = checker.getTypeOfSymbolAtLocation(property, declaration)
      if (property.flags & ts.SymbolFlags.Method) {
        for (const signature of propertyType.getCallSignatures()) visitSignature(position, signature, depth)
        continue
      }
      const annotation = (ts.isPropertyDeclaration(declaration) || ts.isPropertySignature(declaration)) ? declaration.type
        : ts.isGetAccessorDeclaration(declaration) ? declaration.type : undefined
      visit(position, propertyType, annotation, depth)
    }
    for (const signature of type.getCallSignatures()) visitSignature(`${owner}()`, signature, depth)
    // `{ [key: string]: T }` exposes T as much as a named property does.
    for (const index of checker.getIndexInfosOfType(type)) {
      const declaration = index.declaration
      if (declaration !== undefined && !inSdk(declaration)) continue
      visit(`${owner}[${checker.typeToString(index.keyType)}]`, index.type, declaration?.type, depth)
    }
    // A base type (`extends` / `implements`) is part of the surface too; its
    // members are walked above, a generated base is reported here.
    const symbol = type.getSymbol()
    for (const declaration of symbol?.declarations ?? []) {
      if (!inSdk(declaration) || !(ts.isInterfaceDeclaration(declaration) || ts.isClassDeclaration(declaration))) continue
      for (const clause of declaration.heritageClauses ?? []) {
        for (const heritage of clause.types) {
          const generated = symbolGenerated(checker.getSymbolAtLocation(heritage.expression))
          if (generated !== undefined) record(`${owner} extends`, generated)
        }
      }
    }
  }

  function walkSymbol(owner: string, symbol: ts.Symbol): void {
    if (walkedSymbols.has(symbol)) return
    walkedSymbols.add(symbol)
    const declaration = symbol.valueDeclaration ?? symbol.declarations?.[0]
    if (declaration === undefined) return
    if (symbol.flags & ts.SymbolFlags.Class) {
      walkStructure(owner, checker.getDeclaredTypeOfSymbol(symbol), 0)
      const constructorType = checker.getTypeOfSymbolAtLocation(symbol, declaration)
      for (const signature of constructorType.getConstructSignatures()) visitSignature(`${owner}.constructor`, signature, 0)
      for (const property of constructorType.getProperties()) {
        const member = property.valueDeclaration
        if (member === undefined || property.getName() === 'prototype' || !isPublic(member) || !inSdk(member)) continue
        visit(`${owner}.${property.getName()}`, checker.getTypeOfSymbolAtLocation(property, member), undefined, 0)
      }
      return
    }
    if (symbol.flags & (ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias)) {
      const declared = checker.getDeclaredTypeOfSymbol(symbol)
      // Another name for an SDK class or interface: that type is walked under its own name.
      const named = declared.getSymbol()
      if (symbol.flags & ts.SymbolFlags.TypeAlias && named !== undefined && named !== symbol && named.flags & (ts.SymbolFlags.Class | ts.SymbolFlags.Interface) && sdkDeclared(named)) {
        queue.push({ owner: named.getName(), symbol: named })
        return
      }
      walkStructure(owner, declared, 0)
      return
    }
    if (symbol.flags & (ts.SymbolFlags.Function | ts.SymbolFlags.Variable)) {
      walkStructure(owner, checker.getTypeOfSymbolAtLocation(symbol, declaration), 0)
    }
  }

  for (const entry of entries) {
    const source = program.getSourceFile(entry)
    const module = source === undefined ? undefined : checker.getSymbolAtLocation(source)
    if (module === undefined) throw new Error(`cannot load ${entry}`)
    for (const exported of checker.getExportsOfModule(module)) {
      const target = resolve(exported)
      const members = target.flags & ts.SymbolFlags.ValueModule
        ? checker.getExportsOfModule(target).map((inner) => ({ name: `${exported.getName()}.${inner.getName()}`, symbol: resolve(inner) }))
        : [{ name: exported.getName(), symbol: target }]
      for (const { name, symbol } of members) {
        if (symbolGenerated(symbol) !== undefined) found.add(`export ${name}`)
        else queue.push({ owner: name, symbol })
      }
    }
  }
  for (let next = queue.shift(); next !== undefined; next = queue.shift()) walkSymbol(next.owner, next.symbol)
  return found
}

it('exports no generated type and uses none in a public signature', () => {
  const found = generatedOnThePublicSurface()
  expect({ unexpected: [...found].filter((entry) => !ALLOWED.has(entry)).sort() }).toEqual({ unexpected: [] })
  expect({ stale: [...ALLOWED].filter((entry) => !found.has(entry)).sort() }).toEqual({ stale: [] })
}, 120_000)
