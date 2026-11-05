/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, Project, SyntaxKind } from 'ts-morph';
import { dirname, join, win32 } from 'node:path';
import type { Change, Dependency, Edit, Item, Mapping } from './model.js';
import { applyEdits } from './edits.js';
import { relativePath, text } from './discovery.js';
import { UpgradeError } from './model.js';
import { readContext } from './read-failures.js';
import type { ReadContext } from './read-failures.js';
import type { DeclaredDependency } from './v0-version.js';

export function manifests(files: readonly string[], mapping: Mapping, context: string | ReadContext): {
  readonly changes: readonly Change[]; readonly dependencies: readonly Dependency[]; readonly items: readonly Item[];
  readonly directories: readonly string[]; readonly found: boolean; readonly dependencyDirectories: ReadonlySet<string>;
  readonly declared: readonly DeclaredDependency[];
} {
  const { root, failures } = readContext(context);
  const changes: Change[] = [];
  const dependencies: Dependency[] = [];
  const items: Item[] = [];
  const directories = new Set<string>();
  let found = false;
  const dependencyDirectories = new Set<string>();
  const declared: DeclaredDependency[] = [];
  const parser = new Project({ useInMemoryFileSystem: true });
  for (const file of files.filter(file => win32.basename(file) === 'package.json')) {
    const before = text(file, failures);
    if (failures?.has(file)) continue;
    // JSON.parse rejects malformed JSON; the AST supplies exact source offsets.
    try { JSON.parse(before.replace(/^\uFEFF/, '')); }
    catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      throw new UpgradeError(`Cannot parse ${relativePath(root, file)}: ${error.message}. Fix the manifest before retrying.`);
    }
    const prefix = 'const manifest = (';
    const source = parser.createSourceFile('/manifest.ts', `${prefix}${before});`, { overwrite: true });
    const object = source.getFirstDescendantByKind(SyntaxKind.ObjectLiteralExpression);
    if (!object) throw new UpgradeError(`Invalid package.json: ${relativePath(root, file)}`);
    const dependencySections = object.getProperties().filter(Node.isPropertyAssignment)
      .filter(section => ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'].includes(section.getName().replaceAll('"', '')))
      .map(section => section.getInitializer()).filter(Node.isObjectLiteralExpression);
    const hasRoot = dependencySections.some(entries => entries.getProperties().some(entry =>
      Node.isPropertyAssignment(entry) && entry.getName().replaceAll('"', '') === mapping.packages.ts.new));
    const edits: Edit[] = [];
    const sections: { readonly name: string; readonly entries: typeof object; readonly constraint: boolean }[] = [];
    const constraints = (entries: typeof object): void => {
      sections.push({ name: 'overrides', entries, constraint: true });
      for (const entry of entries.getProperties().filter(Node.isPropertyAssignment)) {
        if (mapping.packages.ts.old.includes(entry.getName().replaceAll('"', ''))) continue;
        const nested = entry.getInitializer();
        if (Node.isObjectLiteralExpression(nested)) constraints(nested);
      }
    };
    for (const section of object.getProperties().filter(Node.isPropertyAssignment)) {
      const name = section.getName().replaceAll('"', '');
      const entries = section.getInitializer();
      if (!Node.isObjectLiteralExpression(entries)) continue;
      if (['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'].includes(name)) sections.push({ name, entries, constraint: false });
      if (name === 'overrides' || name === 'resolutions') constraints(entries);
      if (name === 'pnpm') {
        const overrides = entries.getProperties().find(property => Node.isPropertyAssignment(property) && property.getName().replaceAll('"', '') === 'overrides');
        const value = Node.isPropertyAssignment(overrides) ? overrides.getInitializer() : undefined;
        if (Node.isObjectLiteralExpression(value)) constraints(value);
      }
    }
    for (const { name: sectionName, entries, constraint } of sections) {
      for (const entry of entries.getProperties()) {
        if (!Node.isPropertyAssignment(entry)) continue;
        const name = entry.getName().replaceAll('"', '');
        const version = entry.getInitializer();
        const alias = Node.isStringLiteral(version) && mapping.packages.ts.old.some(packageName =>
          version.getLiteralValue() === `npm:${packageName}` || version.getLiteralValue().startsWith(`npm:${packageName}@`));
        const reference = constraint && Node.isStringLiteral(version) &&
          mapping.packages.ts.old.some(packageName => version.getLiteralValue() === `$${packageName}`);
        const qualified = constraint && mapping.packages.ts.old.some(packageName => name.startsWith(`${packageName}@`));
        const scoped = constraint && mapping.packages.ts.old.some(packageName => name.endsWith(`/${packageName}`) || name.includes(`/${packageName}@`));
        if (!mapping.packages.ts.old.includes(name) && !qualified && !reference && !scoped && !alias) continue;
        found = true;
        if (!constraint) dependencyDirectories.add(dirname(file));
        // What the project installs (peer ranges describe consumers, not this install).
        if (!constraint && sectionName !== 'peerDependencies' && Node.isStringLiteral(version)) {
          const literal = version.getLiteralValue();
          declared.push({ file, range: alias ? literal.replace(/^npm:(?:@[^@/]+\/)?[^@]+@?/, '') : literal });
        }
        const detail = { file: relativePath(root, file), line: source.getLineAndColumnAtPos(entry.getStart()).line,
          column: Math.max(1, source.getLineAndColumnAtPos(entry.getStart()).column -
            (source.getLineAndColumnAtPos(entry.getStart()).line === 1 ? prefix.length : 0)) };
        // Only ordinary version ranges are safe to replace; keep custom sources
        // and qualified selectors intact and visible for manual reconciliation.
        if (scoped || alias) {
          items.push({ ...detail, kind: 'manual-dependency', message: `Update Daytona package constraint ${name} manually for SDK 2.x.` });
          continue;
        }
        if (qualified || reference || Node.isStringLiteral(version) &&
            !/^[\s~^<>=|*v\d.xX+-]+$/.test(version.getLiteralValue())) {
          const catalog = Node.isStringLiteral(version) && version.getLiteralValue().startsWith('catalog:') ? catalogFile(file, files) : undefined;
          items.push({ ...detail, ...(catalog ? { file: relativePath(root, catalog), line: 0, column: 0 } : {}),
            kind: constraint ? 'config-field' : 'manual-dependency', message: catalog
              ? `Update Daytona package constraint ${name} manually for SDK 2.x (referenced from ${relativePath(root, file)}).`
              : `Update Daytona package constraint ${name} manually for SDK 2.x.` });
          continue;
        }
        if (!Node.isStringLiteral(version)) {
          if (!constraint) throw new UpgradeError(`Invalid dependency version: ${relativePath(root, file)}`);
          items.push({ ...detail, kind: 'config-field', message: `Update non-string Daytona package constraint ${name} manually for SDK 2.x.` });
          continue;
        }
        if (sectionName === 'peerDependencies') {
          items.push({ ...detail, kind: 'peer-dependency', message: `Update peer dependency ${name} manually for SDK 2.x.` });
          continue;
        }
        if (name === mapping.packages.ts.new && version.getLiteralValue() === mapping.packages.ts.range) continue;
        const collision = constraint ? entries.getProperties().some(candidate => Node.isPropertyAssignment(candidate) &&
          candidate.getName().replaceAll('"', '') === mapping.packages.ts.new) : hasRoot;
        if (name !== mapping.packages.ts.new && collision) {
          items.push({ ...detail, kind: 'config-field', message: 'Both Daytona package names are present; reconcile the duplicate dependency manually.' });
          continue;
        }
        edits.push({ start: entry.getNameNode().getStart() - prefix.length, end: entry.getNameNode().getEnd() - prefix.length,
          text: JSON.stringify(mapping.packages.ts.new) });
        edits.push({ start: version.getStart() - prefix.length, end: version.getEnd() - prefix.length, text: JSON.stringify(mapping.packages.ts.range) });
        dependencies.push({ file: detail.file, from: `${name}@${version.getLiteralValue()}`, to: `${mapping.packages.ts.new}@${mapping.packages.ts.range}` });
        directories.add(dirname(file));
      }
    }
    const after = applyEdits(before, edits);
    if (after !== before) changes.push({ file, before, after });
  }
  return { changes, dependencies, items, directories: [...directories], found, dependencyDirectories, declared };
}
function catalogFile(consumer: string, files: readonly string[]): string | undefined {
  let directory = dirname(consumer);
  while (true) {
    const candidate = join(directory, 'pnpm-workspace.yaml');
    if (files.includes(candidate)) return candidate;
    const parent = dirname(directory);
    if (parent === directory) return undefined;
    directory = parent;
  }
}
