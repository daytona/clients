/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, extname, join, relative, resolve, win32 } from 'node:path';
import { Project, ModuleKind, ModuleResolutionKind, ScriptTarget, ts } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import type { ReadFailures } from './read-failures.js';
import type { ReadContext } from './read-failures.js';
import type { Item } from './model.js';
import { compareRank, configRanks } from './config-ownership.js';
import type { ProjectGroup } from './config-ownership.js';
import { gitEnvironment } from './safety.js';

const skipped = new Set(['node_modules', '.git', '.venv', 'venv', 'build', 'dist', 'out', 'coverage', '.next', '.output', 'storybook-static']);
const extensions = new Set(['.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs']);
export function walk(root: string, failures?: ReadFailures): readonly string[] {
  const visit = (directory: string): readonly string[] => {
    const entries = failures ? failures.read(directory, () => readdirSync(directory, { withFileTypes: true })) ?? []
      : readdirSync(directory, { withFileTypes: true });
    return entries.filter(entry => !entry.isSymbolicLink() && !(entry.isDirectory() && skipped.has(entry.name))).flatMap(entry => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? visit(path) : entry.isFile() ? [path] : [];
    });
  };
  return [...visit(root)].sort();
}
export function ignoredSources(files: readonly string[], root: string): ReadonlySet<string> {
  const env = gitEnvironment();
  let directory = resolve(root);
  while (!existsSync(join(directory, '.git')) && dirname(directory) !== directory) directory = dirname(directory);
  const candidates = files.filter(file => extensions.has(extname(file)) && !/^\.env/.test(win32.basename(file)) &&
    !/(?:^|\/)(?:deploy|k8s|helm|charts|\.github\/workflows)\//.test(relativePath(directory, file)));
  if (!existsSync(join(directory, '.git')) || !candidates.length) return new Set();
  const input = candidates.map(file => resolve(file)).join('\0') + '\0';
  const ignored = spawnSync('git', ['-C', root, 'check-ignore', '--stdin', '-z'], {
    input, encoding: 'utf8', env, maxBuffer: Math.max(1024 * 1024, Buffer.byteLength(input) + 1),
  });
  return new Set(ignored.status === 0 ? ignored.stdout.split('\0').filter(Boolean)
    .map(file => process.platform === 'win32' ? file.replaceAll('\\', '/') : file) : []);
}
export function analysisFiles(files: readonly string[], context: ReadContext): {
  readonly files: readonly string[]; readonly skippedFiles: readonly string[]; readonly items: readonly Item[];
} {
  const items: Item[] = [];
  const skippedFiles: string[] = [];
  const selected = files.filter(file => {
    if (!extensions.has(extname(file))) return true;
    const input = text(file, context.failures);
    let sourceMap = false;
    if (input.includes('sourceMappingURL')) {
      const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, ts.LanguageVariant.Standard, input);
      for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
        if ((token === ts.SyntaxKind.SingleLineCommentTrivia || token === ts.SyntaxKind.MultiLineCommentTrivia) &&
          /^(?:\/\/[#@]|\/\*[#@])\s*sourceMappingURL=/.test(scanner.getTokenText())) { sourceMap = true; break; }
      }
    }
    const lines = input.split(/\r\n|[\r\n\u2028\u2029]/u);
    const generated = /\.min\.[cm]?[jt]sx?$/i.test(file) || sourceMap;
    const longLines = lines.some(line => line.length >= 10000);
    if (!generated && (!longLines || /daytona|(?:api|server)url/i.test(input))) return true;
    // Report all module imports conservatively, including local aliases and workspace packages.
    if (!generated || /daytona/i.test(input) || ts.preProcessFile(input, true, true).importedFiles.length) items.push({ file: relativePath(context.root, file), line: 0, column: 0, kind: 'unsupported-file',
      message: generated
        ? `${relativePath(context.root, file)} looks generated or minified and mentions Daytona or imports another module; review it or its generator.`
        : 'Skipped source with long lines; review it if it uses Daytona.' });
    skippedFiles.push(file);
    return false;
  });
  return { files: selected, skippedFiles, items };
}
export function projects(root: string, files: readonly string[], failures?: ReadFailures): readonly ProjectGroup[] {
  const claimed = new Set<string>();
  // ts-morph canonicalizes Windows paths to forward slashes, unlike walk().
  const paths = files.map(file => process.platform === 'win32' || !existsSync(file) ? file.replaceAll('\\', '/') : file);
  const result: ProjectGroup[] = [];
  const fileSystem = failures?.fileSystem();
  const configs = new Map<string, ts.ParsedCommandLine>();
  const visited = new Set<string>();
  const visitConfig = (config: string): void => {
    if (visited.has(config)) return;
    visited.add(config);
    const input = ts.readConfigFile(config, path => text(path, failures));
    if (!input.config || failures?.has(config)) return;
    const parsed = ts.parseJsonConfigFileContent(input.config, ts.sys, dirname(config), { allowJs: true });
    for (const reference of parsed.projectReferences ?? []) visitConfig(ts.resolveProjectReferencePath(reference));
    // At equal depth the referenced app config takes precedence over its solution config.
    configs.set(config, parsed);
  };
  for (const config of paths.filter(file => /^(?:ts|js)config(?:\.[^.]+)*\.json$/.test(win32.basename(file)))) visitConfig(config);
  const owners = new Map<string, string>();
  const ranks = new Map<string, readonly number[]>();
  for (const [config, parsed] of configs) for (const [file, rank] of configRanks(config, parsed)) {
    if (!paths.includes(file)) continue;
    if (!ranks.has(file) || compareRank(rank, ranks.get(file) ?? []) > 0) {
      owners.set(file, config);
      ranks.set(file, rank);
    }
  }
  for (const [config, parsed] of configs) {
    const project = new Project({ tsConfigFilePath: config, skipAddingFilesFromTsConfig: true, compilerOptions: { allowJs: true }, ...(fileSystem ? { fileSystem } : {}) });
    const included = new Set(parsed.fileNames);
    for (const file of paths.filter(file => included.has(file) && extensions.has(extname(file)))) project.addSourceFileAtPath(file);
    const sources = project.getSourceFiles().filter(source => {
      const file = source.getFilePath();
      return owners.get(file) === config && paths.includes(file) && extensions.has(extname(file)) && !failures?.has(file);
    });
    for (const source of sources) claimed.add(source.getFilePath());
    result.push({ project, sources, included });
  }
  const fallback = new Project({ ...(fileSystem ? { fileSystem } : {}), compilerOptions: {
    allowJs: true, checkJs: true, target: ScriptTarget.ESNext, module: ModuleKind.NodeNext,
    moduleResolution: ModuleResolutionKind.NodeNext, skipLibCheck: true, noEmit: true,
  } });
  const sources = paths.filter(file => extensions.has(extname(file)) && !claimed.has(file))
    .map(file => fallback.addSourceFileAtPath(file)).filter(source => !failures?.has(source.getFilePath()));
  if (sources.length) result.push({ project: fallback, sources, included: new Set(sources.map(source => source.getFilePath())) });
  return result;
}
export type InstallCommand = { readonly cwd: string; readonly manager: string; readonly args: readonly string[] };
export function installCommand(directory: string, root: string = directory): InstallCommand {
  let current = resolve(directory);
  const boundary = resolve(root);
  while (true) {
    const manager = [
      ['package-lock.json', 'npm'], ['npm-shrinkwrap.json', 'npm'], ['yarn.lock', 'yarn'],
      ['pnpm-lock.yaml', 'pnpm'], ['bun.lockb', 'bun'], ['bun.lock', 'bun'],
    ].find(([file]) => file !== undefined && existsSync(join(current, file)))?.[1];
    if (manager) return { cwd: current, manager, args: ['install'] };
    const parent = dirname(current);
    if (current === boundary || parent === current) return { cwd: resolve(directory), manager: 'npm', args: ['install'] };
    current = parent;
  }
}
export function relativePath(root: string, path: string): string { return relative(root, path).replaceAll('\\', '/'); }
export function text(path: string, failures?: ReadFailures): string {
  // Analysis may replace invalid bytes; only files with planned edits must be UTF-8.
  return failures ? failures.read(path, () => readFileSync(path, 'utf8')) ?? '' : readFileSync(path, 'utf8');
}
