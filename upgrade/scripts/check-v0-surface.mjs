#!/usr/bin/env node
/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFile, readdir } from 'node:fs/promises';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, join, dirname } from 'node:path';
import { parseArgs } from 'node:util';
import { createRequire } from 'node:module';
import { collectRootTsExports } from './root-ts-exports.mjs';
import { tsExports } from './v0-ts-exports.mjs';

const { values } = parseArgs({ options: { src: { type: 'string' }, 'write-v0-exports': { type: 'boolean' } } });
if (!values.src) {
  console.error('Usage: node check-v0-surface.mjs --src <daytona/clients checkout>');
  process.exit(1);
}
const mappingPath = new URL('../mapping/mapping.json', import.meta.url);
const mapping = JSON.parse(await readFile(mappingPath, 'utf8'));
const schema = JSON.parse(await readFile(new URL('../mapping/mapping.schema.json', import.meta.url), 'utf8'));

// Deliberately implements only the schema vocabulary used by mapping.schema.json.
function validate(value, rule, path = '$') {
  if (rule.$ref) return validate(value, schema.$defs[rule.$ref.split('/').at(-1)], path);
  const errors = [];
  const type = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
  if (rule.type && ![rule.type].flat().includes(type)) return [`${path}: expected ${rule.type}`];
  if ('const' in rule && value !== rule.const) errors.push(`${path}: expected ${JSON.stringify(rule.const)}`);
  if (rule.enum && !rule.enum.includes(value)) errors.push(`${path}: invalid enum value ${value}`);
  if (type === 'string') {
    if (rule.minLength && value.length < rule.minLength) errors.push(`${path}: empty string`);
    if (rule.pattern && !new RegExp(rule.pattern).test(value)) errors.push(`${path}: invalid format`);
  }
  if (type === 'array') {
    if (rule.uniqueItems && new Set(value.map((item) => JSON.stringify(item))).size !== value.length) errors.push(`${path}: duplicate items`);
    if (rule.items) value.forEach((item, i) => errors.push(...validate(item, rule.items, `${path}[${i}]`)));
  }
  if (type === 'object') {
    for (const key of rule.required ?? []) if (!(key in value)) errors.push(`${path}: missing ${key}`);
    for (const [key, item] of Object.entries(value)) {
      const rules = [rule.properties?.[key], ...Object.entries(rule.patternProperties ?? {}).filter(([pattern]) => new RegExp(pattern).test(key)).map(([, child]) => child)].filter(Boolean);
      if (rules.length === 0 && rule.additionalProperties === false) errors.push(`${path}: unknown property ${key}`);
      for (const child of rules) errors.push(...validate(item, child, `${path}.${key}`));
    }
    for (const [key, dependencies] of Object.entries(rule.dependentRequired ?? {})) {
      if (key in value) for (const dependency of dependencies) if (!(dependency in value)) errors.push(`${path}.${key}: requires ${dependency}`);
    }
  }
  for (const child of rule.allOf ?? []) errors.push(...validate(value, child, path));
  if (rule.if && validate(value, rule.if, path).length === 0 && rule.then) errors.push(...validate(value, rule.then, path));
  return errors;
}
const problems = validate(mapping, schema);
if (problems.length > 0) {
  console.error(problems.join('\n'));
  process.exit(1);
}
const ts = createRequire(new URL('../../sdk-typescript/package.json', import.meta.url))('typescript');

async function sources(directory, extension) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(entries.map(async (entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return ['__tests__', 'tests', '__pycache__', 'node_modules'].includes(entry.name) ? [] : sources(path, extension);
    return entry.name.endsWith(extension) ? [{ path, text: await readFile(path, 'utf8') }] : [];
  }));
  return files.flat();
}
function classes(files, language) {
  const tokens = language === 'py'
    ? /"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|#[^\n]*/g
    : /\/\*[\s\S]*?\*\/|\/\/[^\n]*|`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/g;
  const result = new Map();
  for (const { path, text } of files) {
    const clean = text.replace(tokens, (token) => token.replace(/[^\n]/g, ' '));
    const declarations = language === 'py'
      ? /^class\s+(\w+)(?:\(([^)]*)\))?\s*:/gm
      : /\b(?:export\s+)?(?:abstract\s+)?class\s+(\w+)([^\n{]*)\{/g;
    for (const match of clean.matchAll(declarations)) {
      const start = match.index + match[0].length;
      let body;
      if (language === 'py') body = clean.slice(start).split(/\n(?=\S)/)[0];
      else {
        let depth = 1;
        let end = start;
        while (end < clean.length && depth > 0) {
          if (clean[end] === '{') depth++;
          if (clean[end] === '}') depth--;
          end++;
        }
        body = clean.slice(start, end - 1);
      }
      const members = new Set();
      const staticMethods = new Set();
      if (language === 'ts') {
        const declaration = ts.createSourceFile(path, `class Surface {${text.slice(start, start + body.length)}}`, ts.ScriptTarget.Latest, true)
          .statements.find(ts.isClassDeclaration);
        for (const member of declaration?.members ?? []) {
          if (!ts.isPropertyDeclaration(member) || !ts.isIdentifier(member.name)) continue;
          const modifiers = new Set(member.modifiers?.map((modifier) => modifier.kind));
          if (modifiers.has(ts.SyntaxKind.PrivateKeyword) || modifiers.has(ts.SyntaxKind.ProtectedKeyword)) continue;
          const name = member.name.text;
          if (!/^(?:[a-zA-Z]|_experimental_)/.test(name) && !modifiers.has(ts.SyntaxKind.PublicKeyword)) continue;
          let value = member.initializer;
          while (value && ts.isParenthesizedExpression(value)) value = value.expression;
          let type = member.type;
          while (type && ts.isParenthesizedTypeNode(type)) type = type.type;
          const callable = value && (ts.isArrowFunction(value) || ts.isFunctionExpression(value)) ||
            type && (ts.isFunctionTypeNode(type) || ts.isTypeLiteralNode(type) && type.members.some(ts.isCallSignatureDeclaration));
          if (callable) members.add(name);
          if (callable && modifiers.has(ts.SyntaxKind.StaticKeyword)) staticMethods.add(name);
        }
      }
      let depth = 0;
      const lines = body.split('\n');
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const method = language === 'py'
          ? /^ {4}(?:async\s+)?def\s+((?:[a-zA-Z]|_experimental_)\w*)\s*\(/.exec(line)
          : depth === 0 ? /^\s*(?:(?:public|static|async|override)\s+)*((?:[a-zA-Z]|_experimental_)\w*)\s*(?:<[^;{]*>)?\s*\(/.exec(line) ?? /^\s*public\s+(?:(?:static|async|override)\s+)*(_[a-zA-Z]\w*)\s*(?:<[^;{]*>)?\s*\(/.exec(line) : null;
        if (method && method[1] !== 'constructor') {
          let decorator = i - 1;
          while (decorator >= 0 && /^\s*(?:@|$)/.test(lines[decorator])) decorator--;
          const decorators = lines.slice(decorator + 1, i).join('\n');
          if (!/@(?:\w+\.)?property\b|@\w+\.(?:setter|getter)\b/.test(decorators)) members.add(method[1]);
          if (/\bstatic\b/.test(line.slice(0, line.indexOf(method[1]))) || /@staticmethod\b|@classmethod\b/.test(decorators)) staticMethods.add(method[1]);
        }
        if (language === 'ts') depth += [...line].filter((c) => c === '{').length - [...line].filter((c) => c === '}').length;
      }
      const bases = language === 'py' ? (match[2] ?? '').split(',').map((base) => base.trim()) : [...(match[2] ?? '').matchAll(/\bextends\s+(\w+)/g)].map((base) => base[1]);
      if (result.has(match[1]) && !match[1].startsWith('_')) problems.push(`${language}: ambiguous class ${match[1]} in ${path}`);
      result.set(match[1], { members, staticMethods, bases, body, path });
    }
  }
  return result;
}
function publicMethods(index, name, visited = new Set()) {
  if (visited.has(name) || !index.has(name)) return new Set();
  visited.add(name);
  const cls = index.get(name);
  return new Set([...cls.members, ...cls.bases.flatMap((base) => [...publicMethods(index, base, visited)])]);
}
function compare(actual, expected, label) {
  for (const name of actual) if (!expected.has(name)) problems.push(`${label}: missing ${name}`);
  for (const name of expected) if (!actual.has(name)) problems.push(`${label}: nonexistent ${name}`);
}
const src = resolve(values.src);
const python = await sources(join(src, 'sdk-python/src/daytona'), '.py');
const typescript = await sources(join(src, 'sdk-typescript/src'), '.ts');
const indexes = { py: classes(python, 'py'), ts: classes(typescript, 'ts') };
const seen = new Set();
const coverage = new Map();
const anchors = new Map();
for (const entry of mapping.suggestions) {
  if (seen.has(entry.id)) problems.push(`duplicate suggestion ${entry.id}`);
  seen.add(entry.id);
  if (entry.id !== `${entry.receiver}.${entry.v0.ts ?? entry.v0.py}`) problems.push(`${entry.id}: inconsistent id`);
  if (entry.v0.py === null && entry.v0.ts === null) problems.push(`${entry.id}: no v0 method`);
  if (entry.category !== 'none' && entry.v2.py === null && entry.v2.ts === null) problems.push(`${entry.id}: no v2 equivalent; use category none`);
  const producesSandbox = mapping.receivers.sandbox.producedBy.includes(entry.id);
  if (entry.startsWorkflow !== producesSandbox) problems.push(`${entry.id}: incorrect startsWorkflow flag`);
  if (!mapping.receivers[entry.receiver]) problems.push(`${entry.id}: unknown receiver`);
  if (anchors.has(entry.receiver) && anchors.get(entry.receiver) !== entry.anchor) problems.push(`${entry.id}: inconsistent section anchor`);
  anchors.set(entry.receiver, entry.anchor);
  for (const language of ['py', 'ts']) {
    const method = entry.v0[language];
    if (method === null) {
      if (entry.v2[language] !== null || entry.notes[language].length) problems.push(`${entry.id}: ${language} has no v0 method but has migration advice`);
      continue;
    }
    const key = `${entry.receiver}:${language}:${method}`;
    coverage.set(key, (coverage.get(key) ?? 0) + 1);
    if (!mapping.receivers[entry.receiver]?.[language].members.includes(method)) problems.push(`${entry.id}: ${language} method ${method} absent from members`);
    if (entry.category === 'direct' && entry.v2[language] === null) problems.push(`${entry.id}: missing direct ${language} v2 equivalent`);
    if (entry.v2[language] === null && entry.notes[language].length === 0) problems.push(`${entry.id}: explain the absent ${language} equivalent`);
  }
}
for (const [receiver, metadata] of Object.entries(mapping.receivers)) {
  if (metadata.parent && !mapping.receivers[metadata.parent]) problems.push(`${receiver}: unknown parent ${metadata.parent}`);
  for (const language of ['py', 'ts']) {
    const parents = new Set([receiver]);
    let ancestor = metadata;
    while (ancestor?.parent) {
      if (metadata[language].members.length && ancestor.attr[language] === null) problems.push(`${receiver}: missing ${language} ancestor attribute`);
      if (parents.has(ancestor.parent)) { problems.push(`${receiver}: receiver parent cycle`); break; }
      parents.add(ancestor.parent);
      ancestor = mapping.receivers[ancestor.parent];
    }
    const index = indexes[language];
    const union = new Set();
    const surfaces = [];
    for (const type of metadata[language].types) {
      if (!index.has(type)) problems.push(`${receiver}: ${language} class ${type} not found`);
      const methods = publicMethods(index, type);
      surfaces.push({ type, methods });
      for (const method of methods) union.add(method);
    }
    compare(union, new Set(metadata[language].members), `${receiver}.${language}.members`);
    if (language === 'py' && surfaces.some(({ methods }) => [...union].some((method) => !methods.has(method))) && !metadata.notes?.length) problems.push(`${receiver}: document Python sync/async method differences in notes`);
    for (const method of union) {
      const count = coverage.get(`${receiver}:${language}:${method}`) ?? 0;
      // The root close() closes both namespace clients in 2.x; a v2-namespace suggestion would leak the v0 client.
      if (receiver === 'client' && method === 'close') {
        if (count !== 0) problems.push(`client.close (${language}): the root close() stays; do not map it to a v2 suggestion`);
        continue;
      }
      if (count !== 1) problems.push(`${receiver}.${method} (${language}): expected one suggestion, found ${count}`);
    }
  }
}
if (new Set(anchors.values()).size !== anchors.size) problems.push('Section anchors must be unique across receivers');
for (const language of ['py', 'ts']) {
  const index = indexes[language];
  const moved = new Set();
  for (const type of mapping.receivers.client[language].types) {
    const lineage = new Set([type]);
    for (const name of lineage) for (const base of index.get(name)?.bases ?? []) lineage.add(base);
    const ancestors = [...lineage].map((name) => index.get(name)).filter(Boolean);
    const staticMethods = new Set(ancestors.flatMap((cls) => [...cls.staticMethods]));
    // close remains on the 2.x root; static helpers are not instance members.
    for (const method of publicMethods(index, type)) if (method !== 'close' && !staticMethods.has(method)) moved.add(method);
    const body = ancestors.map((cls) => cls.body).join('\n');
    const services = language === 'py'
      ? /self\.(\w+)(?:\s*:\s*\w+)?\s*=\s*\w*Service\s*\(/g
      : /^ {2}(?:(?:public|readonly|declare)\s+)*(\w+)\s*[!:]?\s*:\s*\w*Service\b|this\.(\w+)\s*=\s*new\s+\w*Service\s*\(/gm;
    const privateNames = new Set([...body.matchAll(/^\s*(?:private|protected)\s+(?:readonly\s+)?(\w+)/gm)].map((match) => match[1]));
    for (const match of body.matchAll(services)) if (!(match[1] ?? match[2]).startsWith('_') && !privateNames.has(match[1] ?? match[2])) moved.add(match[1] ?? match[2]);
    // Public instance attributes the 0.x client sets for callers (Python
    // `self.default_language`): they move to `.v0` like methods do.
    if (language === 'py') for (const match of body.matchAll(/^ {8}self\.([a-z]\w*)\s*(?::[^=\n]+)?=(?!=)/gm)) moved.add(match[1]);
  }
  compare(moved, new Set(mapping.movedClientMembers[language]), `${language}.movedClientMembers`);
}
const init = python.find(({ path }) => path === join(src, 'sdk-python/src/daytona/__init__.py')).text;
const exportedPython = new Set([...(/__all__\s*=\s*\[([\s\S]*?)\]/.exec(init)?.[1] ?? '').matchAll(/["'](\w+)["']/g)].map((match) => match[1]));
const exportedTs = tsExports(typescript, join(src, 'sdk-typescript/src/index.ts'));
// v0Exports: the pinned 0.x root's public names, which the tools check before
// moving an import to the v0 namespace (an older 0.x name the bundle lacks
// would otherwise move to a namespace that does not have it).
// v0Version: the pinned release itself, which the tools compare with the
// project's installed 0.x version (older warns, a newer minor refuses).
const pinnedVersion = readFileSync(new URL('../../scripts/v0-sdk-version', import.meta.url), 'utf8').split('\n')[0].trim().replace(/^v/, '');
if (values['write-v0-exports']) {
  mapping.v0Exports = { py: [...exportedPython].sort(), ts: [...exportedTs].sort() };
  mapping.v0Version = pinnedVersion;
  writeFileSync(mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
}
if (mapping.v0Version !== pinnedVersion) problems.push(`v0Version: ${mapping.v0Version} is not the pinned ${pinnedVersion} (scripts/v0-sdk-version)`);
for (const [language, names] of [['py', exportedPython], ['ts', exportedTs]]) {
  compare(names, new Set(mapping.v0Exports?.[language] ?? []), `${language}.v0Exports`);
}
// rootNames are the names that stay importable from the 2.x root: 0.x root
// exports that the 2.x root also has, plus names only the 2.x root exports
// (DaytonaV2Client, V0_API_URL, ...), read from this checkout's SDK roots.
const sdkRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const rootPython = readFileSync(join(sdkRoot, 'sdk-python/src/daytona/__init__.py'), 'utf8');
const rootPythonNames = new Set([
  ...[...(/__all__\s*=\s*\[([\s\S]*?)\]/.exec(rootPython)?.[1] ?? '').matchAll(/["'](\w+)["']/g)].map((match) => match[1]),
  ...[...rootPython.matchAll(/^\s*from \._client import (\w+) as \1/gm)].map((match) => match[1]),
  'v0',
  'v2',
]);
const rootTs = collectRootTsExports(readFileSync(join(sdkRoot, 'sdk-typescript/src/index.ts'), 'utf8'));
const rootTsNames = rootTs.names;
problems.push(...rootTs.problems);
for (const [language, names, root] of [['py', exportedPython, rootPythonNames], ['ts', exportedTs, rootTsNames]]) {
  for (const name of mapping.rootNames[language]) if (!names.has(name) && !root.has(name)) problems.push(`${language}.rootNames: ${name} is neither a v0 nor a 2.x root export`);
  for (const name of root) if (!mapping.rootNames[language].includes(name)) problems.push(`${language}.rootNames: missing 2.x root export ${name}`);
}
// A former root alias is a v0 export (0.x code imports it) that names a shared
// root category the 2.x root still exports.
for (const [alias, category] of Object.entries(mapping.rootAliasCategories ?? {})) {
  for (const [language, names, root] of [['py', exportedPython, rootPythonNames], ['ts', exportedTs, rootTsNames]]) {
    if (!names.has(alias)) problems.push(`rootAliasCategories: ${alias} is not a ${language} v0 export`);
    if (root.has(alias)) problems.push(`rootAliasCategories: ${alias} is still a ${language} 2.x root export`);
    if (!root.has(category)) problems.push(`rootAliasCategories: ${category} is not a ${language} 2.x root export`);
  }
}
if (problems.length) {
  console.error(problems.join('\n'));
  process.exitCode = 1;
} else console.log(`v0 surface covered: ${Object.keys(mapping.receivers).length} receivers, ${mapping.suggestions.length} suggestions; schema valid`);
