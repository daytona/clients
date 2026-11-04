/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { ts } from 'ts-morph';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

function syntaxErrors(text: string, fileName = 'index.ts') {
  return ts.transpileModule(text, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.Preserve } }).diagnostics ?? [];
}
const fixmeSource = "import { Daytona } from '@daytonaio/sdk';\ndeclare function getClient(options: unknown): unknown;\ngetClient(\n  { timeout: 1 }\n).create();\n";
const suggestionSource = "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\n(\n d\n).v0.create();\n";
const multilineMapping = { ...suggestionMapping, suggestions: suggestionMapping.suggestions.map(entry => ({ ...entry,
  v2: { ts: entry.v2.ts === null ? null : entry.v2.ts + '\n' }, notes: { ts: ['first\nsecond\rthird\u2028fourth'] },
})) };
it('keeps a generic multiline receiver parseable without unrelated SDK review hints', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': fixmeSource });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  const after = contents(root)['index.ts'] ?? '';
  expect(syntaxErrors(after)).toEqual([]);
  expect(after.split('\n').filter(line => line.includes('FIXME(daytona-upgrade)'))).toHaveLength(0);
  expect(after).toBe(fixmeSource.replace('@daytonaio/sdk', '@daytona/sdk'));
});
it('does not duplicate a FIXME when its multiline receiver is analyzed again', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': fixmeSource });
  await invoke(root);
  const before = contents(root);
  // When
  await invoke(root);
  // Then
  expect(contents(root)).toEqual(before);
});
it('emits only removable tagged comments when suggestion expressions and notes contain newlines', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': suggestionSource });
  // When
  const result = await invoke(root, ['--suggest-v2'], multilineMapping);
  // Then
  expect(result.code).toBe(0);
  expect(syntaxErrors(contents(root)['index.ts'] ?? '')).toEqual([]);
});
it('replaces multiline-expression suggestions without changing their second output', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': suggestionSource });
  await invoke(root, ['--suggest-v2'], multilineMapping);
  const before = contents(root);
  // When
  await invoke(root, ['--suggest-v2'], multilineMapping);
  // Then
  expect(contents(root)).toEqual(before);
});
it('restores the original source when multiline-expression suggestions are removed', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': suggestionSource });
  await invoke(root, ['--suggest-v2'], multilineMapping);
  // When
  await invoke(root, ['--remove-suggestions']);
  // Then
  expect(contents(root)['index.ts']).toBe(suggestionSource);
});
it.each(['--remove-suggestions', '--suggest-v2'])('preserves runtime data and block comments when %s handles tagged-looking text', async mode => {
  // Given
  const data = 'const text = `hello\n// daytona-v2: template data\n${1}\n// daytona-v2: tail data\nbye`;\n' +
    'const quoted = "hello' + '\\' + '\n// daytona-v2: quoted data' + '\\' + '\nbye";\n' +
    'const view = <pre>\n// daytona-v2: JSX data\n</pre>;\n' +
    '/*\n// daytona-v2: block comment\n*/\nconst n = 1; // daytona-v2: inline user comment\n';
  const before = "import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\n" + data + '// daytona-v2: remove me\nawait d.v0.list();\n';
  const root = temporaryProject({ 'index.tsx': before });
  // When
  const result = await invoke(root, [mode], suggestionMapping);
  // Then
  expect(result.code).toBe(0);
  const after = contents(root)['index.tsx'] ?? '';
  expect(after).toContain(data);
  expect(after).not.toContain('// daytona-v2: remove me');
  expect(syntaxErrors(after, 'index.tsx')).toEqual([]);
});
it('preserves a template value when removal finds no real tool comments or Daytona code', async () => {
  // Given
  const before = 'const text = `hello\n// daytona-v2: runtime data\nbye`;\n';
  const root = temporaryProject({ 'index.ts': before });
  // When
  const result = await invoke(root, ['--remove-suggestions']);
  // Then
  expect(result.code).toBe(2);
  expect(contents(root)['index.ts']).toBe(before);
});
it('removes an EOF tool comment when it is the only token in the file', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': '// daytona-v2: stale' });
  // When
  const result = await invoke(root, ['--remove-suggestions']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe('');
});
