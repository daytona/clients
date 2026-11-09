#!/usr/bin/env node
/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { hasOptionalArguments, withNamedArguments, withoutOptionalArguments } from './optional-arguments.mjs';

const sdkRoot = fileURLToPath(new URL('../../', import.meta.url));

function noteForms(entry, language) {
  // Notes contain partial examples and prose, not compilable call signatures.
  // Check only qualified calls or backticked members on known SDK receivers.
  const roots = {
    client: '{client}', sandbox: '{sandbox}',
    fs: '{sandbox}.fs', git: '{sandbox}.git', process: '{sandbox}.process',
    pty: '{sandbox}.pty', lsp: '{sandbox}.lsp', images: '{client}.v2.images',
    handle: '{sandbox}.process.connect("id")',
    session: `{sandbox}.process.${language === 'py' ? 'connect_session' : 'connectSession'}("id")`,
  };
  const forms = new Set();
  for (const note of entry.notes?.[language] ?? []) {
    for (const [, receiver, members] of note.matchAll(/(?<![\w.])(\{(?:client|sandbox)\}|[A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)+)(?=\s*\(|`)/g)) {
      const root = receiver.startsWith('{') ? receiver : Object.hasOwn(roots, receiver) ? roots[receiver] : undefined;
      if (root) forms.add(`${root}${members}`);
    }
  }
  return [...forms];
}

async function main() {
  const { values } = parseArgs({ options: { mapping: { type: 'string' } } });
  const mapping = JSON.parse(readFileSync(values.mapping ?? new URL('../mapping/mapping.json', import.meta.url), 'utf8'));
  // The existing v0 gate owns the full mapping schema.
  if (!Array.isArray(mapping.suggestions) || mapping.suggestions.some((entry) =>
    typeof entry.id !== 'string' || !entry.v2 || ['py', 'ts'].some((lang) =>
      entry.v2[lang] !== null && typeof entry.v2[lang] !== 'string'))) {
    throw new TypeError('Expected suggestions with id and nullable py/ts v2 forms');
  }
  let checkTsForms;
  try {
    ({ checkTsForms } = await import('./v2-typescript-surface.mjs'));
  } catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'MODULE_NOT_FOUND'
      || !error.message.startsWith("Cannot find module 'typescript'\n")) throw error;
    console.error("Missing TypeScript dependency; run 'npm ci' in sdk-typescript/ first");
    process.exitCode = 1;
    return;
  }
  const notes = mapping.suggestions.flatMap((entry) => ['py', 'ts'].flatMap((language) =>
    noteForms(entry, language).map((form, index) => ({
      ...entry, id: `${entry.id} notes.${language}[${index}]`,
      v2: { py: null, ts: null, [language]: form },
    }))));
  // A form with optional arguments is checked both ways: every argument
  // passed (by its v0 name) and every one left out.
  const forms = mapping.suggestions.flatMap((entry) => {
    const optional = ['py', 'ts'].filter((lang) => entry.v2[lang] !== null && hasOptionalArguments(entry.v2[lang]));
    if (optional.length === 0) return [entry];
    const variant = (render) => Object.fromEntries(['py', 'ts'].map((lang) => [lang, entry.v2[lang] === null ? null : render(entry.v2[lang])]));
    return [
      { ...entry, v2: variant(withNamedArguments) },
      { ...entry, id: `${entry.id} (defaults)`, v2: variant(withoutOptionalArguments) },
    ];
  });
  const expanded = { ...mapping, suggestions: [...forms, ...notes] };
  const python = spawnSync('python3', [fileURLToPath(new URL('./v2-python-surface.py', import.meta.url)), sdkRoot], {
    input: JSON.stringify(expanded), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
  });
  if (python.error) throw python.error;
  if (python.status !== 0) {
    process.stderr.write(python.stderr);
    process.exitCode = 1;
    return;
  }
  const py = JSON.parse(python.stdout);
  const ts = checkTsForms(expanded, sdkRoot);
  const problems = [...py.problems, ...ts.problems];
  if (problems.length) {
    console.error(problems.join('\n'));
    process.exitCode = 1;
  } else {
    const count = (language) => mapping.suggestions.filter((entry) => entry.v2[language] !== null).length;
    console.log(`v2 surface bound: ${count('py')} py forms, ${count('ts')} ts forms`);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
