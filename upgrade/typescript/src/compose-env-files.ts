/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isAlias, isMap, isNode, isScalar, isSeq, LineCounter, parseDocument } from 'yaml';
import { MAX_ENVIRONMENT_BYTES } from './environment-text.js';

type Reference = { readonly value: string | undefined; readonly line: number; readonly column: number };
type Include = {
  readonly paths: readonly Reference[]; readonly envFiles: readonly Reference[];
  readonly projectDirectory: Reference | undefined;
};

/** Parse Compose references without evaluating interpolation or expanding aliases. */
export function composeEnvFiles(lines: readonly string[], explicit = false, truncated = false): {
  readonly compose: boolean; readonly references: readonly Reference[]; readonly includes: readonly Include[];
} {
  const candidate = lines.some(line => /^(?:services|include|'(?:services|include)'|"(?:services|include)")\s*:/.test(line)) ||
    /^\s*\{/.test(lines.find(line => !/^\s*(?:#.*|---(?:\s+#.*)?)?$/.test(line)) ?? '');
  if (!explicit && !candidate) return { compose: false, references: [], includes: [] };
  const text = lines.join('\n');
  if (truncated || Buffer.byteLength(text) > MAX_ENVIRONMENT_BYTES) return {
    compose: true, references: [{ value: undefined, line: 0, column: 0 }], includes: [],
  };
  const counter = new LineCounter();
  const document = parseDocument(text, { lineCounter: counter, logLevel: 'silent' });
  const include = isMap(document.contents) ? document.contents.get('include', true) : undefined;
  const entries = isSeq(include) ? include.items : [include];
  const composeInclude = include !== undefined && entries.every(entry =>
    isScalar(entry) && typeof entry.value === 'string' || isMap(entry) && entry.items.every(pair =>
      isScalar(pair.key) && ['path', 'env_file', 'project_directory'].includes(String(pair.key.value))));
  const compose = explicit || isMap(document.contents) &&
    (isMap(document.contents.get('services', true)) || composeInclude);
  if (!compose) return { compose: false, references: [], includes: [] };
  if (document.errors.length) return { compose, references: [{ value: undefined, line: 0, column: 0 }], includes: [] };
  const references: Reference[] = [];
  const reference = (node: unknown): Reference => {
    const position = counter.linePos(isNode(node) ? node.range?.[0] ?? 0 : 0);
    return { value: isScalar(node) && typeof node.value === 'string' ? node.value : undefined,
      line: position.line, column: position.col };
  };
  const add = (node: unknown): void => { references.push(reference(node)); };
  const list = (node: unknown): readonly Reference[] => (isSeq(node) ? node.items : [node]).map(reference);
  const includes: Include[] = [];
  if (isMap(document.contents)) for (const pair of document.contents.items) {
    if (isAlias(pair.key)) {
      const key = pair.key.resolve(document);
      if (!isScalar(key) || key.value === 'include' || key.value === '<<') add(pair.key);
      continue;
    }
    if (!isScalar(pair.key) || pair.key.value !== 'include') continue;
    for (const entry of isSeq(pair.value) ? pair.value.items : [pair.value]) {
      if (!isMap(entry)) { includes.push({ paths: list(entry), envFiles: [], projectDirectory: undefined }); continue; }
      for (const property of entry.items) {
        if (isAlias(property.key) || isScalar(property.key) && property.key.value === '<<') add(property.key);
      }
      includes.push({ paths: list(entry.get('path', true)),
        envFiles: entry.has('env_file') ? list(entry.get('env_file', true)) : [],
        projectDirectory: entry.has('project_directory') ? reference(entry.get('project_directory', true)) : undefined });
    }
  }
  const services = isMap(document.contents) ? document.contents.get('services', true) : undefined;
  if (isMap(services)) for (const service of services.items) {
    if (isAlias(service.value)) { add(service.value); continue; }
    if (!isMap(service.value)) continue;
    for (const pair of service.value.items) {
      // Resolve only scalar key names; never expand aliased loader contents.
      if (isAlias(pair.key)) {
        const key = pair.key.resolve(document);
        if (!isScalar(key) || key.value === 'env_file' || key.value === '<<') add(pair.key);
        continue;
      }
      if (isScalar(pair.key) && pair.key.value === '<<') { add(undefined); continue; }
      if (!isScalar(pair.key) || pair.key.value !== 'env_file') continue;
      const entries = isSeq(pair.value) ? pair.value.items : [pair.value];
      for (const entry of entries) {
        if (isMap(entry)) {
          const path = entry.items.find(property => isScalar(property.key) && property.key.value === 'path');
          add(path?.value);
        } else add(entry);
      }
    }
  }
  return { compose, references, includes };
}
