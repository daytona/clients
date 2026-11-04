/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isBuiltin } from 'node:module';
import { ts } from 'ts-morph';
import type { Project, SourceFile } from 'ts-morph';

const dependencies = new WeakMap<Project, ReadonlySet<string>>();
export function registerProjectDependencies(project: Project, names: ReadonlySet<string>): void {
  dependencies.set(project, names);
}

export function resolveProjectModule(source: SourceFile, name: string): SourceFile | undefined {
  const project = source.getProject();
  const resolved = ts.resolveModuleName(name, source.getFilePath(), project.getCompilerOptions(), project.getModuleResolutionHost()).resolvedModule;
  return resolved ? project.getSourceFile(resolved.resolvedFileName) ?? project.addSourceFileAtPathIfExists(resolved.resolvedFileName) : undefined;
}

export function matchesProjectSpecifier(source: SourceFile, name: string, workspaceNames: readonly string[]): boolean {
  const aliases = Object.keys(source.getProject().getCompilerOptions().paths ?? {}).filter(alias => alias !== '*');
  return /^\.{1,2}\//.test(name) || workspaceNames.some(workspace => name === workspace || name.startsWith(`${workspace}/`)) ||
    matchesPathAlias(name, aliases);
}

export function matchesPathAlias(name: string, aliases: readonly string[]): boolean {
  return aliases.some(alias => {
    const star = alias.indexOf('*');
    return star < 0 ? name === alias : name.length >= alias.length - 1 &&
      name.startsWith(alias.slice(0, star)) && name.endsWith(alias.slice(star + 1));
  });
}

export function isProjectLocalModule(source: SourceFile, name: string, workspaceNames: readonly string[]): boolean {
  if (isBuiltin(name)) return false;
  const resolved = resolveProjectModule(source, name);
  if (resolved) return isProjectSourcePath(resolved.getFilePath());
  const packageName = name.split('/').slice(0, name.startsWith('@') ? 2 : 1).join('/');
  if (!/^\.{1,2}\//.test(name) && dependencies.get(source.getProject())?.has(packageName)) return false;
  return matchesProjectSpecifier(source, name, workspaceNames);
}

export function isProjectSourcePath(path: string): boolean {
  return !path.replaceAll('\\', '/').includes('/node_modules/');
}
