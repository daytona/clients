/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, readFileSync, statSync } from 'node:fs';
import { isUtf8 } from 'node:buffer';
import { createInterface } from 'node:readline/promises';
import { relative } from 'node:path';
import { Node, SyntaxKind, ts } from 'ts-morph';
import type { Change, Item, Mapping, Options, Report } from './model.js';
import { assertNever, UpgradeError } from './model.js';
import { walk, analysisFiles, ignoredSources, projects, installCommand, relativePath, text } from './discovery.js';
import { registerOriginal } from './edits.js';
import { configs, findConfigs, UNRESOLVED_CONFIG } from './config.js';
import { upgrade } from './upgrade.js';
import { suggest, removeSuggestionLines } from './suggestions.js';
import { manifests } from './manifests.js';
import { dependencyScopeItem } from './dependency-scope.js';
import { scan } from './scans.js';
import { gitSafety } from './safety.js';
import { hasDaytonaImport } from './bindings.js';
import { writeChanges } from './write-changes.js';
import { ENV_CUSTOM_URL, sourceEnvUrls } from './env-api-url.js';
import { ReadFailures } from './read-failures.js';
import { sourceSupport } from './source-support.js';
import { skippedSourceUrls } from './source-safety.js';
import { dependencyPackageNames, workspacePackageNames } from './workspace-names.js';
import { registerProjectDependencies } from './project-modules.js';
import { configConflictItems } from './config-ownership.js';
import { registerDaytonaEvidence } from './project-daytona.js';
import { environmentLoads } from './environment-loads.js';
import { environmentFileReferences } from './environment-file-references.js';
import { version } from './sdk-target.js';
import { ancestorDeclarations, checkV0Version } from './v0-version.js';

export { version } from './sdk-target.js';
export function emptyReport(options: Options): Report {
  return { tool: 'daytona-upgrade', version: version(), language: 'ts', mode: options.mode, root: options.root,
    dryRun: options.dry || options.print || (options.mode === 'suggest-v2' && options.json),
    changedFiles: [], dependencies: [], items: [], nextSteps: [] };
}
export type RunResult = { readonly report: Report; readonly changes: readonly Change[]; readonly code: number };
export async function run(options: Options, mapping: Mapping): Promise<RunResult> {
  const base = emptyReport(options);
  if (!existsSync(options.root)) {
    return { report: { ...base, refused: { reason: 'bad-arguments', message: `Path does not exist: ${options.root}` } }, changes: [], code: 2 };
  }
  if (!statSync(options.root).isDirectory()) {
    return { report: { ...base, refused: { reason: 'bad-arguments', message: 'Project path is a file; pass the directory that contains it (the project root).' } }, changes: [], code: 2 };
  }
  const safety = base.dryRun ? {} : gitSafety(options.root, options.force);
  if (safety.refusal) return { report: { ...base, refused: safety.refusal }, changes: [], code: 2 };
  const failures = new ReadFailures(options.root);
  const readContext = { root: options.root, failures };
  const files = walk(options.root, failures);
  const ignored = ignoredSources(files, options.root);
  // ts-morph canonicalizes literal POSIX backslashes too. Analyze the other
  // files, but block writes rather than silently skip a potential custom URL.
  const unsupportedPaths: Item[] = process.platform === 'win32' ? [] : files
    .filter(file => file.includes('\\') && /(?:\.(?:[cm]?[jt]sx?)|(?:ts|js)config\.json)$/.test(file))
    .map(file => ({ file: relative(options.root, file), line: 0, column: 0, kind: 'unsupported-file',
      message: 'Cannot analyze a POSIX path containing a backslash; rename the file or directory before retrying.' }));
  const selected = analysisFiles(files, readContext);
  const skippedFiles = new Set(selected.skippedFiles);
  const ignoredDaytona = new Set([...ignored].filter(file => !skippedFiles.has(file) && /daytona/i.test(text(file, failures))));
  const loadedGroups = projects(options.root, selected.files.filter(file => process.platform === 'win32' || !file.includes('\\')), failures);
  // Safety-only files must not enter the rewrite projects' cross-file binding indexes.
  const safetyGroups = options.mode === 'upgrade'
    ? projects(options.root, selected.skippedFiles.filter(file => (process.platform === 'win32' || !file.includes('\\')) &&
      /daytona/i.test(text(file, failures))), failures) : [];
  const originals = new Map(loadedGroups.flatMap(group => group.sources.map(source => {
    const original = text(source.getFilePath(), failures);
    registerOriginal(source, original);
    return [source.getFilePath(), original] as const;
  })));
  const excludedSources = new Set<string>();
  const conflicts = configConflictItems(loadedGroups, options.root);
  for (const group of loadedGroups) for (const source of group.sources) {
    if (conflicts.some(conflict => conflict.file === relativePath(options.root, source.getFilePath()))) excludedSources.add(source.getFilePath());
  }
  const workspaceNames = workspacePackageNames(files, readContext);
  const dependencyNames = dependencyPackageNames(files, readContext);
  for (const group of [...loadedGroups, ...safetyGroups]) registerProjectDependencies(group.project, dependencyNames);
  let blockingParseErrors = false;
  const parseErrors: Item[] = loadedGroups.flatMap(group => group.sources.flatMap(source => {
    const support = sourceSupport(source, options.root, workspaceNames);
    if (!support.supported) excludedSources.add(source.getFilePath());
    blockingParseErrors ||= support.blocking;
    return support.items;
  }));
  const groups = loadedGroups.map(group => ({ ...group,
    sources: group.sources.filter(source => !excludedSources.has(source.getFilePath())) }));
  const dependency = manifests(files, mapping, readContext);
  registerDaytonaEvidence(loadedGroups.map(group => group.project), [...loadedGroups, ...safetyGroups].flatMap(group => group.sources),
    dependency.dependencyDirectories.size > 0);
  const environmentContext = { mapping, dropApiUrl: options.dropApiUrl };
  const loads = options.mode === 'upgrade' ? environmentLoads([...loadedGroups, ...safetyGroups].flatMap(group => group.sources), readContext) : undefined;
  const references = options.mode === 'upgrade' ? environmentFileReferences(files,
    [...loadedGroups, ...safetyGroups].flatMap(group => group.sources), { ...readContext, ...environmentContext }) : undefined;
  const envFiles = new Set([...(loads?.files ?? []), ...(references?.files ?? [])]);
  const loadItems = [...new Map([...(loads?.items ?? []), ...(references?.items ?? [])].map(item =>
    [JSON.stringify(item), item])).values()];
  const items: Item[] = [...unsupportedPaths, ...selected.items, ...parseErrors, ...conflicts, ...(options.mode === 'upgrade' ? [...dependency.items,
    ...loadItems, ...scan([...new Set([...files, ...envFiles, ...(references?.composeFiles ?? [])])], readContext,
      { ...environmentContext, envFiles, composeFiles: references?.composeFiles ?? new Set() })] : [])];
  if (options.mode === 'upgrade') for (const group of loadedGroups) for (const source of group.sources) {
    items.push(...sourceEnvUrls(source, options.root, environmentContext));
    if (conflicts.some(conflict => conflict.file === relativePath(options.root, source.getFilePath()))) {
      items.push(...configs(source, mapping, { root: options.root, dropApiUrl: options.dropApiUrl }).items
        .filter(item => item.kind === 'custom-api-url'));
    }
  }
  if (options.mode === 'upgrade') for (const group of safetyGroups) for (const source of group.sources) {
    items.push(...skippedSourceUrls(source, options.root, environmentContext));
  }
  const changes: Change[] = options.mode === 'upgrade' ? [...dependency.changes] : [];
  let found = dependency.found;
  const rewrittenSources = new Set<string>();
  const reportedOutsideProviders = new Set<string>();
  const unresolvedFiles: string[] = [];
  const configObjects = groups.flatMap(group => group.sources.flatMap(source => findConfigs(source, mapping)));
  const sources = new Set(groups.flatMap(group => group.sources.filter(source => !ignored.has(source.getFilePath()))));
  for (const group of groups) {
    for (const source of group.sources) {
      let unresolved = false;
      const importsDaytona = hasDaytonaImport(source, mapping);
      found ||= importsDaytona;
      for (const literal of [...source.getDescendantsOfKind(SyntaxKind.StringLiteral), ...source.getDescendantsOfKind(SyntaxKind.NoSubstitutionTemplateLiteral)]) {
        if (![...mapping.v0Modules.ts.old, mapping.v0Modules.ts.v0].includes(literal.getLiteralValue())) continue;
        const parent = literal.getParent();
        if (Node.isImportDeclaration(parent)) {
          const bindings = [parent.getDefaultImport(), parent.getNamespaceImport(),
            ...parent.getNamedImports().map(specifier => specifier.getAliasNode() ?? specifier.getNameNode())].filter(Node.isIdentifier);
          // An unused import needs no receiver/type fallback, even without node_modules.
          if (bindings.length && bindings.every(binding => {
            const symbol = binding.getSymbol();
            // A local use already proves this import is used. Avoid asking the
            // language service to search the entire project for every holder.
            const usedLocally = symbol !== undefined && source.getDescendantsOfKind(SyntaxKind.Identifier).some(reference =>
              reference.getFirstAncestorByKind(SyntaxKind.ImportDeclaration) !== parent && reference.getSymbol() === symbol);
            return !usedLocally && binding.findReferencesAsNodes().every(reference =>
              reference.getFirstAncestorByKind(SyntaxKind.ImportDeclaration) === parent);
          })) continue;
        }
        if (!ts.resolveModuleName(literal.getLiteralValue(), source.getFilePath(), group.project.getCompilerOptions(), ts.sys).resolvedModule) unresolved = true;
      }
      const before = originals.get(source.getFilePath()) ?? source.getFullText();
      let after: string;
      switch (options.mode) {
        case 'upgrade': {
          const result = upgrade(source, mapping, { ...options, sources, workspaceNames, reportedOutsideProviders, configObjects: [...new Map(configObjects
            .filter(object => object.getSourceFile().getFilePath() === source.getFilePath())
            .map(object => [object.getStart(), object])).values()] });
          after = result.after; items.push(...result.items); break;
        }
        case 'suggest-v2': {
          const result = suggest(source, mapping, options.root);
          after = result.after; items.push(...result.items); break;
        }
        case 'remove-suggestions': after = removeSuggestionLines(source); found ||= after !== source.getFullText(); break;
        default: assertNever(options.mode);
      }
      if (before.startsWith('\uFEFF') && !after.startsWith('\uFEFF')) after = '\uFEFF' + after;
      if (after !== before && ignored.has(source.getFilePath())) ignoredDaytona.add(source.getFilePath());
      if (after !== before && !ignored.has(source.getFilePath())) {
        changes.push({ file: source.getFilePath(), before, after });
        if (options.mode === 'upgrade') rewrittenSources.add(source.getFilePath());
      }
      if (unresolved && options.mode !== 'remove-suggestions' && !ignored.has(source.getFilePath())) unresolvedFiles.push(relativePath(options.root, source.getFilePath()));
    }
  }
  const ignoredPaths = [...ignoredDaytona].map(file => relativePath(options.root, file)).sort();
  const ignoredList = ignoredPaths.slice(0, 5).join(', ') + (ignoredPaths.length > 5 ? ` and ${ignoredPaths.length - 5} more` : '');
  if (ignoredPaths.length) items.push({ file: ignoredPaths[0] ?? '.', line: 0, column: 0, kind: 'fixme',
    message: `${ignoredPaths.length} Git-ignored source file${ignoredPaths.length === 1 ? ' mentions Daytona or would change but was' : 's mention Daytona or would change but were'} not changed: ${ignoredList}; review ${ignoredPaths.length === 1 ? 'it or its generator' : 'them or their generators'}.` });
  if (unresolvedFiles.length) items.push({ file: unresolvedFiles[0] ?? '', line: 0, column: 0, kind: 'types-unresolved',
    message: `Daytona types could not be resolved in ${unresolvedFiles.length} file${unresolvedFiles.length === 1 ? '' : 's'}; using syntactic fallback. Install dependencies for stronger analysis.` });
  for (const rewrittenSource of rewrittenSources) {
    const item = dependencyScopeItem(options.root, rewrittenSource, dependency.dependencyDirectories, mapping.packages.ts.range);
    if (item && !items.some(existing => existing.kind === item.kind && existing.file === item.file)) items.push(item);
  }
  const invalidEditedFiles: Item[] = changes.filter(change => {
    const bytes = failures.read(change.file, () => readFileSync(change.file));
    return bytes !== undefined && !isUtf8(bytes);
  }).map(change => {
    const file = relativePath(options.root, change.file);
    return { file, line: 0, column: 0, kind: 'unsupported-file',
      message: `${file} is not valid UTF-8; convert it to UTF-8, then rerun.` };
  });
  items.push(...failures.items);
  if (blockingParseErrors || items.some(item => item.kind === 'private-import' ||
    item.kind === 'unsupported-file' && !selected.items.includes(item) && !parseErrors.includes(item))) found = true;
  if (items.some(item => item.kind === 'custom-api-url')) {
    const knownCustom = items.some(item => item.kind === 'custom-api-url' && item.message !== UNRESOLVED_CONFIG);
    const environmentRefusal = items.some(item => item.kind === 'custom-api-url' && item.message === ENV_CUSTOM_URL) &&
      !items.some(item => item.kind === 'custom-api-url' && item.message !== ENV_CUSTOM_URL && item.message !== UNRESOLVED_CONFIG);
    return { changes: [], code: 2, report: { ...base, items,
      nextSteps: knownCustom ? [] : ['rerun with --drop-api-url if every DaytonaConfig targets Daytona Cloud'],
      refused: { reason: 'custom-api-url', message: knownCustom
        ? (environmentRefusal ? ENV_CUSTOM_URL : 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.') + ' No files were written.'
        : 'Cannot verify that every DaytonaConfig targets Daytona Cloud. If they do, rerun with --drop-api-url; for a custom API URL, stay on SDK 0.x. No files were written.' } } };
  }
  const v0 = options.mode === 'upgrade' && found
    ? checkV0Version(options.root, dependency.declared.length ? dependency.declared : ancestorDeclarations(options.root, mapping.packages.ts.old),
      mapping.v0Version, options.allowNewerV0 ?? false) : { warnings: [] };
  const warnings = v0.warnings.length ? { warnings: v0.warnings } : {};
  if (v0.refusal !== undefined) return { changes: [], code: 2, report: { ...base, items, ...warnings, refused: { reason: 'newer-v0', message: v0.refusal } } };
  // A custom API URL (above) refuses with exit 2 before unsupported files exit 1.
  if (invalidEditedFiles.length) return { changes: [], code: 1, report: { ...base, items: [...items, ...invalidEditedFiles], ...warnings } };
  if (unsupportedPaths.length || failures.items.length || blockingParseErrors) return { changes: [], code: 1,
    report: { ...base, items, ...warnings, ...(blockingParseErrors ? { nextSteps: ['Fix source parse errors before retrying.'] } : {}) } };
  if (!found) return { changes: [], code: 2, report: { ...base, items, refused: { reason: 'no-daytona', message: 'No Daytona dependency or code found.' } } };
  const commands = options.mode === 'upgrade' ? [...new Map(dependency.directories.map(directory => {
    const command = installCommand(directory, options.root); return [command.cwd, command];
  })).values()] : [];
  const nextSteps = [...(safety.note ? [safety.note] : []), ...commands.map(command =>
    `Run ${command.manager} ${command.args.join(' ')} in ${relativePath(process.cwd(), command.cwd) || '.'}`)];
  if (options.mode === 'upgrade') nextSteps.push('Review manual items, then run your type checker and tests. Use --suggest-v2 when ready to move workflows to v2.');
  const report: Report = { ...base, changedFiles: changes.map(change => relativePath(options.root, change.file)).sort(),
    dependencies: options.mode === 'upgrade' ? dependency.dependencies : [], items: items.map(item =>
      selected.items.includes(item) && dependency.changes.length && options.mode === 'upgrade'
        ? { ...item, message: `${item.message} The Daytona dependency is being updated to 2.x; this file needs manual migration.` } : item), nextSteps, ...warnings };
  const manual = items.some(item => item.kind !== 'suggestion' && item.kind !== 'workflow');
  const code = options.strict && manual ? 1 : 0;
  if (!report.dryRun && changes.length) {
    if (!options.yes && process.stdin.isTTY) {
      const prompt = createInterface({ input: process.stdin, output: process.stderr });
      try {
        const answer = await prompt.question(`Change ${changes.length} files? [y/N] `);
        if (!/^y(?:es)?$/i.test(answer.trim())) return { report: { ...report, dryRun: true }, changes, code };
      } finally { prompt.close(); }
    }
    writeChanges(changes);
  }
  return { report, changes, code };
}
