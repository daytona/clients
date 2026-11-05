/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process';
import { gitEnvironment } from './safety.js';
import { existsSync } from 'node:fs';
import { dirname, join, parse, resolve, win32 } from 'node:path';
import type { Item } from './model.js';
import { relativePath } from './discovery.js';
import { envUrlItems } from './env-api-url.js';
import { environmentKeyPosition, environmentSettingLiteral } from './environment-setting.js';
import { environmentText } from './environment-text.js';
import type { EnvironmentContext } from './env-api-url.js';
import { readContext } from './read-failures.js';
import type { ReadContext } from './read-failures.js';
import { YamlFlow, yamlBlockScalar, yamlBlockScalarBodies, yamlContinuation } from './yaml-flow.js';
import { companionCommand, version } from './sdk-target.js';

function dockerEnvironment(lines: readonly string[], file: string, context: EnvironmentContext): readonly Item[] {
  const items: Item[] = [];
  const heredocs: { readonly delimiter: string; readonly tabs: boolean }[] = [];
  const record = (line: number, column: number, setting: { readonly name: string; readonly literal: string | undefined }): void => {
    if (setting.literal !== '') items.push(...envUrlItems({ file, line, column }, setting, context));
  };
  for (let index = 0; index < lines.length; index++) {
    const start = index;
    let line = lines[index] ?? '';
    const heredoc = heredocs[0];
    if (heredoc) {
      if ((heredoc.tabs ? line.replace(/^\t+/, '') : line) === heredoc.delimiter) heredocs.shift();
      continue;
    }
    if (line.trimStart().startsWith('#')) continue;
    while (line.endsWith('\\') && index + 1 < lines.length) {
      const next = lines[++index] ?? '';
      if (!next.trimStart().startsWith('#')) line = line.slice(0, -1) + next;
    }
    const bare = line.match(/^\s*(DAYTONA_(?:API|SERVER)_URL)\s*=/)?.[1];
    if (bare) {
      record(start + 1, line.indexOf(bare) + 1, { name: bare, literal: undefined });
      continue;
    }
    const instruction = line.match(/^\s*(?:ONBUILD\s+)?(\w+)\s+(.*)$/i);
    const command = instruction?.[1]?.toUpperCase();
    const argumentsText = instruction?.[2];
    if (argumentsText === undefined) continue;
    if (command === 'RUN' || command === 'COPY' || command === 'ADD') {
      for (const token of argumentsText.matchAll(/"(?:\\.|[^"\\])*"|'[^']*'|(?<!<)<<(-?)[ \t]*(?:"([^"\\]+)"|'([^']+)'|([\w.-]+))(?=\s|$|[;&|])/g)) {
        const delimiter = token[2] ?? token[3] ?? token[4];
        if (delimiter !== undefined) heredocs.push({ delimiter, tabs: token[1] === '-' });
      }
      // Malformed heredocs cannot certify that later lines are payload. Only
      // suppress a body when every delimiter has a matching terminator.
      let cursor = index + 1;
      for (const heredoc of heredocs) {
        while (cursor < lines.length && (heredoc.tabs ? lines[cursor]?.replace(/^\t+/, '') : lines[cursor]) !== heredoc.delimiter) cursor++;
        if (cursor === lines.length) { heredocs.length = 0; break; }
        cursor++;
      }
    }
    if (command !== 'ENV' && command !== 'ARG') continue;
    // Tokenize declaration words only, preserving quoted/escaped word fragments.
    // Any unmatched quote/escape leaves a gap and makes the declaration manual.
    const tokens = [...argumentsText.matchAll(/(?:[^\s'"\\]|\\.|'[^']*'|"(?:\\.|[^"\\])*")+/g)];
    let end = 0;
    let complete = true;
    for (const token of tokens) {
      if (argumentsText.slice(end, token.index).trim()) complete = false;
      end = token.index + token[0].length;
    }
    if (!complete || argumentsText.slice(end).trim()) {
      for (const match of line.matchAll(/\bDAYTONA_(?:API|SERVER)_URL\b/g)) {
        record(start + 1, match.index + 1, { name: match[0], literal: undefined });
      }
      continue;
    }
    // The replacer follows JS's callback ABI: whole match plus three mutually
    // exclusive captures, not four independent domain inputs.
    const words = tokens.map(token => token[0].replace(/(\\.)|'([^']*)'|"((?:\\.|[^"\\])*)"/g,
      (_part: string, escape: string | undefined, single: string | undefined, double: string | undefined): string =>
        escape !== undefined ? escape.slice(1) : single ?? (double ?? '').replace(/\\(["\\])/g, '$1')));
    const first = words[0];
    if (first === undefined) continue;
    const pairs = command === 'ENV' && !first.includes('=') ? [first + '=' + words.slice(1).join(' ')] : words;
    for (const [pairIndex, pair] of pairs.entries()) {
      const separator = pair.indexOf('=');
      const key = separator < 0 ? pair : pair.slice(0, separator);
      if (key !== 'DAYTONA_API_URL' && key !== 'DAYTONA_SERVER_URL') continue;
      let value = separator < 0 ? '' : pair.slice(separator + 1);
      const next = pairs[pairIndex + 1];
      if (separator >= 0 && value === '' && next !== undefined && !next.includes('=')) value = next;
      const literal = separator >= 0 && !/\{%/.test(value) ? environmentSettingLiteral(value) : undefined;
      record(start + 1, line.indexOf(key) + 1, { name: key, literal });
    }
  }
  return items;
}

export function scan(files: readonly string[], project: string | ReadContext, context: EnvironmentContext & {
  readonly envFiles?: ReadonlySet<string>;
  readonly composeFiles?: ReadonlySet<string>;
}): readonly Item[] {
  const { root, failures } = readContext(project);
  const items: Item[] = [];
  const env = gitEnvironment();
  let directory = root;
  while (!existsSync(join(directory, '.git')) && dirname(directory) !== directory) directory = dirname(directory);
  const repository = existsSync(join(directory, '.git')) ? spawnSync('git', ['-C', root, 'rev-parse', '--show-toplevel'], { encoding: 'utf8', env: { ...env, LC_ALL: 'C', LANG: 'C' } }) : undefined;
  // When Git cannot answer the whole absolute path is classified: narrowing the scan root
  // must never hide a deployment file, and scanning one extra YAML file is the safe failure.
  const environmentRoot = repository?.status === 0 ? repository.stdout.trim() : parse(resolve(root)).root;
  for (const file of files) {
    const name = win32.basename(file);
    const composeFile = context.composeFiles?.has(file) === true;
    if (/^\.env\.(?:example|sample|template)$/.test(name) && !context.envFiles?.has(file) && !composeFile) continue;
    const path = relativePath(root, file);
    const dotenvFile = !composeFile && (/^\.env/.test(name) || context.envFiles?.has(file) === true);
    if (/^(?:pyproject\.toml|requirements.*\.txt|(?:requirements|constraints).*\.in|setup\.(?:py|cfg)|Pipfile)$/.test(name)) {
      items.push({ file: path, line: 0, column: 0, kind: 'other-ecosystem', message: `Python manifest found; run ${companionCommand(version())} for this ecosystem.` });
    }
    const unsupported = /\.(?:vue|svelte|astro)$/.test(name);
    const environment = dotenvFile || composeFile || /^(?:docker-compose|compose).*\.ya?ml$/.test(name) || /^Dockerfile/.test(name) ||
      /(?:^|\/)(?:k8s|helm|charts|deploy|\.github\/workflows)\/.*\.ya?ml$/.test(relativePath(environmentRoot, file));
    if (!unsupported && !environment) continue;
    const input = environmentText(file, failures);
    if (!input) continue;
    if (input.truncated && environment && !composeFile) items.push(...envUrlItems(
      { file: path, line: 0, column: 0 }, { name: 'DAYTONA_API_URL', literal: undefined }, context));
    const lines = input.text.split(/\r?\n/);
    const yamlFile = !dotenvFile && (composeFile || /\.ya?ml$/i.test(name));
    const blockBodies = yamlFile ? yamlBlockScalarBodies(lines) : [];
    if (!dotenvFile && !composeFile && /^Dockerfile/.test(name)) {
      items.push(...dockerEnvironment(lines.at(-1) === '' ? lines.slice(0, -1) : lines, path, context));
      continue;
    }
    const yamlFlow = new YamlFlow();
    for (const [index, original] of lines.entries()) {
      const comment = [...original.matchAll(/"(?:\\.|[^"\\])*"|'[^']*'|(?<comment>(?<!\S)#)/g)].find(token => token.groups?.['comment']);
      const line = original.slice(0, comment?.index ?? original.length);
      const flow = yamlFlow.line(line);
      if (unsupported && /@daytona(?:io)?\/sdk/.test(line)) items.push({ file: path, line: index + 1, column: 1,
        kind: 'unsupported-file', message: 'Daytona import in an unsupported file; migrate its script manually.' });
      if (environment) for (const match of line.matchAll(/\bDAYTONA_(?:API|SERVER)_URL\b/g)) {
        const suffix = line.slice(match.index + match[0].length);
        if (blockBodies[index] && !/^\s*=/.test(suffix)) continue;
        const prefix = line.slice(0, match.index);
        if (!environmentKeyPosition(prefix)) continue;
        const assigned = suffix.match(/^['"]?\s*(?:=|:)\s*(.*)$/)?.[1];
        const inlineValue = /\bname\s*:\s*['"]?$/.test(prefix) ? suffix.match(/^['"]?\s*,\s*['"]?value['"]?\s*:\s*(.*)$/)?.[1] : undefined;
        const k8sLine = /^\s*-?\s*name\s*:/.test(line) ? lines[index + 1] : undefined;
        const k8sValue = k8sLine?.match(/^\s*value\s*:\s*(.*)$/)?.[1];
        const valueLineIndex = assigned !== undefined ? index : k8sValue !== undefined ? index + 1 : index;
        const valueLine = lines[valueLineIndex] ?? original;
        let value = assigned ?? inlineValue ?? k8sValue;
        let ambiguousContinuation = false;
        let unresolvedBlockScalar = false;
        if (yamlFile && value !== undefined) {
          const [isBlockScalar, blockValue] = yamlBlockScalar(lines, valueLineIndex, valueLine);
          if (isBlockScalar) {
            value = blockValue;
            unresolvedBlockScalar = blockValue === undefined;
          } else if (value.trim() === '') {
            const indent = valueLine.length - valueLine.trimStart().length;
            const [continuation, ambiguous] = yamlContinuation(lines, valueLineIndex + 1, indent);
            if (continuation !== undefined) value = continuation;
            ambiguousContinuation = ambiguous;
          }
        }
        // Quoted list scalars wrap KEY=value; map quotes wrap only the value.
        const listQuote = /^['"]?\s*=/.test(suffix) ? prefix.match(/(?:^\s*(?:-\s*)?|[\[,]\s*)(['"])$/)?.[1] : undefined;
        const yamlValue = dotenvFile ? value ?? '' : (value ?? '').replace(/^(?:[&!][^\s]+\s+)+/, '');
        const scalarText = listQuote ? listQuote + yamlValue : yamlValue;
        const quoted = scalarText.match(/^("(?:\\.|[^"\\])*"|'(?:''|[^'])*')(?=\s*(?:[,}\]]|$))/)?.[1];
        const scalar = quoted ?? (flow[match.index] ? scalarText.match(/^(?:\$\{[^{}]*\}|[^,}\]])*/)?.[0] ?? '' : scalarText);
        const literal = ambiguousContinuation || unresolvedBlockScalar || value === undefined ? undefined : environmentSettingLiteral(dotenvFile ? value : scalar);
        if (literal === '') continue;
        items.push(...envUrlItems({ file: path, line: index + 1, column: match.index + 1 }, { name: match[0], literal }, context));
      }
    }
  }
  return items;
}
