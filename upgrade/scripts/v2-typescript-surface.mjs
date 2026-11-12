/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createRequire } from 'node:module';
import { join } from 'node:path';

// Installed by the mapping CI job; never use emitted declarations from dist.
const require = createRequire(new URL('../../sdk-typescript/package.json', import.meta.url));
const ts = require('typescript');

function walk(node, visit) {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

function rootIdentifier(node) {
  if (ts.isIdentifier(node)) return node.text;
  if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)
    || ts.isCallExpression(node) || ts.isAwaitExpression(node) || ts.isParenthesizedExpression(node)) {
    return rootIdentifier(node.expression);
  }
}

function receiverType(name, entry, receivers) {
  if (name === 'client') return 'RootDaytona';
  if (name === 'sandbox') return 'V2.Sandbox';
  // These v0 receiver names became handles in v2, not same-named services.
  if (entry.receiver === 'ptyHandle') {
    return name === 'connection' ? 'Awaited<ReturnType<V2.ProcessHandle["attach"]>>' : 'V2.ProcessHandle';
  }
  const type = receivers[entry.receiver]?.ts?.types[0];
  return type ? `V2.${type}` : 'unknown';
}

function formSource(entry, receivers) {
  const form = entry.v2.ts.replace(/\{(client|sandbox)\}/g, '$1');
  const source = ts.createSourceFile('form.ts', form, ts.ScriptTarget.Latest, true);
  const references = new Set();
  const locals = new Set();
  const memberRoots = new Set();
  walk(source, (node) => {
    if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
      memberRoots.add(rootIdentifier(node));
    }
    if (!ts.isIdentifier(node)) return;
    const parent = node.parent;
    if ((ts.isVariableDeclaration(parent) || ts.isParameter(parent)) && parent.name === node) {
      locals.add(node.text);
      return;
    }
    if ((ts.isPropertyAccessExpression(parent) && parent.name === node)
      || (ts.isPropertyAssignment(parent) && parent.name === node)) return;
    references.add(node.text);
  });
  const parameters = [...references].filter((name) => !locals.has(name) && name !== 'undefined').map((name) => {
    const isReceiver = memberRoots.has(name) || name === 'client' || name === 'sandbox';
    // Only opaque user DATA arguments get any. SDK receivers must stay typed.
    return `${name}: ${isReceiver ? receiverType(name, entry, receivers) : 'any'}`;
  });
  return `import type { Daytona as RootDaytona } from './index.js';
import type * as V2 from './v2/index.js';
async function check(${parameters.join(', ')}) {\n${form}\n}\n`;
}

export function checkTsForms(mapping, sdkRoot) {
  const directory = join(sdkRoot, 'sdk-typescript');
  const config = ts.readConfigFile(join(directory, 'tsconfig.json'), ts.sys.readFile);
  if (config.error) throw new SyntaxError(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'));
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, directory);
  const options = { ...parsed.options, noEmit: true, noUnusedLocals: false, noUnusedParameters: false };
  const forms = new Map();
  for (const entry of mapping.suggestions) {
    if (entry.v2.ts === null) continue;
    const path = join(directory, 'src', `__v2_surface_${forms.size}.ts`);
    forms.set(path, { entry, source: ts.createSourceFile(path, formSource(entry, mapping.receivers ?? {}), options.target, true) });
  }
  if (!forms.size) return { count: 0, problems: [] };
  const host = ts.createCompilerHost(options);
  const readSource = host.getSourceFile.bind(host);
  const fileExists = host.fileExists.bind(host);
  host.getSourceFile = (...args) => forms.get(args[0])?.source ?? readSource(...args);
  host.fileExists = (path) => forms.has(path) || fileExists(path);
  // One program amortizes parsing the SDK and dependencies across all forms.
  const program = ts.createProgram([...forms.keys()], options, host);
  const problems = [];
  for (const diagnostic of [...program.getOptionsDiagnostics(), ...program.getGlobalDiagnostics()]) {
    problems.push(`ts compiler ${ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ')}`);
  }
  for (const { entry, source } of forms.values()) {
    for (const diagnostic of [...program.getSyntacticDiagnostics(source), ...program.getSemanticDiagnostics(source)]) {
      problems.push(`${entry.id} ts TS${diagnostic.code}: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ')} | ${entry.v2.ts}`);
    }
  }
  return { count: forms.size, problems };
}
