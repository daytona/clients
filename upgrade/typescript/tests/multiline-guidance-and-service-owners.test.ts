/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project } from 'ts-morph';
import { displayText, removeSuggestionLines } from '../src/edits.js';
import { suggest } from '../src/suggestions.js';
import { loadMapping } from '../src/mapping.js';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

it.each(['\r\n', '\r', '\n', '\u2028', '\u2029'])('preserves surrounding code when removing comments separated by %j', async newline => {
  // Given
  const before = `const first = 1;${newline}  // daytona-v2: stale${newline}const last = 2;${newline}`;
  const root = temporaryProject({ 'index.ts': before });
  // When
  await invoke(root, ['--remove-suggestions']);
  // Then
  expect(contents(root)['index.ts']).toBe(`const first = 1;${newline}const last = 2;${newline}`);
});

it.each(['\r\n', '\r', '\n', '\u2028', '\u2029'])('preserves following code when a tool comment begins the file before %j', async newline => {
  // Given
  const kept = `const last = 2;${newline}`;
  const root = temporaryProject({ 'index.ts': `// daytona-v2: stale${newline}${kept}` });
  // When
  await invoke(root, ['--remove-suggestions']);
  // Then
  expect(contents(root)['index.ts']).toBe(kept);
});

it.each(['\r\n', '\r', '\n', '\u2028', '\u2029'])('preserves inline user comments when an EOF tool comment follows %j', newline => {
  // Given
  const kept = `const first = 1; // daytona-v2: user${newline}`;
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('index.ts', `${kept}// daytona-v2: stale`);
  // When
  const after = removeSuggestionLines(source);
  // Then
  expect(after).toBe(kept);
});

it.each(['daytona.v0.snapshot', 'daytona.snapshot'])('uses the owning root when a stable service alias starts at %s', expression => {
  // Given
  const text = `import { Daytona } from '@daytona/sdk';
const daytona = new Daytona();
const service = ${expression};
const alias = service;
alias.get('image');`;
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('index.ts', text);
  // When
  const result = suggest(source, loadMapping(), '/');
  // Then
  expect(result.items.find(item => item.suggestion?.id === 'client.snapshot.get')?.suggestion?.v2)
    .toBe('daytona.v2.images.get(imageId)');
});

it('reports an unproven root when a service is only known by annotation', () => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('index.ts',
    "import { SnapshotService } from '@daytona/sdk/v0';\nfunction use(service: SnapshotService) { service.get('image'); }");
  // When
  const result = suggest(source, loadMapping(), '/');
  // Then
  expect(result.items.map(item => ({ kind: item.kind, suggestion: item.suggestion })))
    .toEqual([{ kind: 'fixme', suggestion: undefined }]);
});

it('reports an unproven root when a service owner is shadowed at its use', () => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('index.ts',
    "import { Daytona } from '@daytona/sdk';\nconst daytona = new Daytona();\nconst service = daytona.v0.snapshot;\nfunction use(daytona: unknown) { service.get('image'); }");
  // When
  const result = suggest(source, loadMapping(), '/');
  // Then
  expect(result.items.map(item => item.kind)).toEqual(['fixme']);
});

it('reports an unproven root when a service captured this from another function', () => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('index.ts',
    "import { Daytona } from '@daytona/sdk';\nclass Owner { client = new Daytona(); use() { const service = this.client.v0.snapshot; return function other() { service.get('image'); }; } }");
  // When
  const result = suggest(source, loadMapping(), '/');
  // Then
  expect(result.items.map(item => item.kind)).toEqual(['fixme']);
});

it.each([
  ['getClient(\n  "a  b\\tc"\n)', 'getClient( "a  b\\tc" )'],
  ['getClient(\n  `a  b ${"c  d"} e  f`\n)', 'getClient( `a  b ${"c  d"} e  f` )'],
  ['getClient(\n /a  b/\n)', 'getClient( /a  b/ )'],
  ['getClient(\n `a\n  b`\n)', 'getClient( `a\n  b` )'],
])('preserves literal contents when displaying %j', (input, expected) => {
  // Given / When
  const displayed = displayText(input);
  // Then
  expect(displayed).toBe(expected);
});

it('preserves multiline template contents when rendering suggestion comments', async () => {
  // Given
  const literal = '`a\n  b`';
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytona/sdk';\ndeclare function getClient(name: string): Daytona;\ngetClient(${literal}).v0.list();\n` });
  // When
  await invoke(root, ['--suggest-v2'], suggestionMapping);
  // Then
  const guidance = (contents(root)['index.ts'] ?? '').split('\n')
    .filter(line => line.startsWith('// daytona-v2: ') && !line.startsWith('// daytona-v2:   docs:'))
    .map(line => line.slice('// daytona-v2: '.length)).join('\n');
  expect(guidance).toBe(`getClient(${literal}).v2.list()`);
});

it('restores multiline-template source when generated suggestions are removed', async () => {
  // Given
  const before = "import { Daytona } from '@daytona/sdk';\ndeclare function getClient(name: string): Daytona;\ngetClient(`a\n  b`).v0.list();\n";
  const root = temporaryProject({ 'index.ts': before });
  await invoke(root, ['--suggest-v2'], suggestionMapping);
  // When
  await invoke(root, ['--remove-suggestions']);
  // Then
  expect(contents(root)['index.ts']).toBe(before);
});

it('does not duplicate a FIXME when an unproven receiver contains a multiline literal', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytonaio/sdk';\ndeclare function getClient(name: string): unknown;\ngetClient(`a\n  b`).create();\n" });
  await invoke(root);
  const before = contents(root);
  // When
  await invoke(root);
  // Then
  expect(contents(root)).toEqual(before);
});

it('preserves literal argument spacing when emitting executable v2 guidance', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': "import { Daytona } from '@daytona/sdk';\ndeclare function getClient(name: string): Daytona;\ngetClient('a  b').v0.list();\n" });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], suggestionMapping);
  // Then
  const report: { readonly items: readonly { readonly suggestion?: { readonly v2: string } }[] } = JSON.parse(result.output);
  expect(report.items.find(item => item.suggestion)?.suggestion?.v2).toBe("getClient('a  b').v2.list()");
});
