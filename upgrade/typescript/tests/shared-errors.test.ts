/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { applyEdits, removeSuggestionLines } from '../src/edits.js';
import { imports } from '../src/imports.js';
import { loadMapping } from '../src/mapping.js';
import { suggest } from '../src/suggestions.js';

const mapping = loadMapping();
// The 0.x root aliases of two shared categories. The 2.x root no longer
// exports them, but bundled v0 does, so they route to v0 like any other error.
const formerRootAliases = ['DaytonaValidationError', 'DaytonaAuthorizationError'] as const;
const errors = [...mapping.rootNames.ts.filter(name => /^Daytona.*Error$/.test(name)), ...formerRootAliases];
const forms = [
  ['named', (name: string) => `import { ${name} as E } from '@daytonaio/sdk';`],
  ['commonjs', (name: string) => `const { ${name}: E } = require('@daytonaio/sdk');`],
  ['namespace', (name: string) => `import * as sdk from '@daytonaio/sdk'; void sdk.${name};`],
  ['commonjs namespace', (name: string) => `const sdk = require('@daytonaio/sdk'); void sdk.${name};`],
  ['import equals', (name: string) => `import sdk = require('@daytonaio/sdk'); void sdk.${name};`],
  ['re-export', (name: string) => `export { ${name} as E } from '@daytonaio/sdk';`],
  ['type import', (name: string) => `import type { ${name} as E } from '@daytonaio/sdk';`],
  ['inline type import', (name: string) => `import { type ${name} as E } from '@daytonaio/sdk';`],
  ['type query', (name: string) => `type E = import('@daytonaio/sdk').${name};`],
  ['require member', (name: string) => `const E = require('@daytonaio/sdk').${name};`],
  ['dynamic member', (name: string) => `const E = (await import('@daytonaio/sdk')).${name};`],
] as const;

it.each(errors.flatMap(name => forms.map(([form, input]) => ({ name, form, input: input(name) }))))(
  'routes $name to v0 when using $form', ({ input }) => {
    // Given
    const project = new Project({ useInMemoryFileSystem: true });
    const source = project.createSourceFile('/input.ts', input);
    // When
    const result = imports(source, mapping, '/');
    const output = applyEdits(input, result.edits);
    // Then
    expect(output).toContain("'@daytona/sdk/v0'");
    expect(result.items).toEqual([]);
    source.replaceWithText(output);
    expect(applyEdits(output, imports(source, mapping, '/').edits)).toBe(output);
    if (input.includes('import type')) expect(output).toContain('import type');
    if (input.includes('void sdk.')) expect(output).toMatch(/void sdkV0\.Daytona\w*Error/);
  },
);

it('splits the root client from shared errors when an import is mixed', () => {
  // Given
  const input = "import { Daytona, DaytonaError } from '@daytonaio/sdk';";
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/input.ts', input);
  // When
  const output = applyEdits(input, imports(source, mapping, '/').edits);
  // Then
  expect(output).toBe("import { Daytona } from '@daytona/sdk';\nimport { DaytonaError } from '@daytona/sdk/v0';");
});

it.each(forms)('attaches removable, idempotent import guidance when using %s', (_form, input) => {
  // Given
  const original = input('DaytonaNotFoundError').replaceAll('@daytonaio/sdk', '@daytona/sdk/v0');
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/input.ts', original);
  // When
  const output = suggest(source, mapping, '/').after;
  // Then: assert the reserved, machine-removable tag, not the prose.
  expect(output.match(/\/\/ daytona-v2:/g)).toHaveLength(1);
  source.replaceWithText(output);
  expect(suggest(source, mapping, '/').after).toBe(output);
  expect(removeSuggestionLines(source)).toBe(original);
});

it('leaves unrelated resource imports without error guidance', () => {
  // Given
  const input = "import { Sandbox } from '@daytona/sdk/v0';";
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/input.ts', input);
  // When
  const result = suggest(source, mapping, '/');
  // Then
  expect(result.after).toBe(input);
});

it.each(formerRootAliases)('keeps %s a v0 export, not a root name', name => {
  expect(mapping.rootNames.ts).not.toContain(name);
  expect(mapping.v0Exports?.ts).toContain(name);
});

it.each([
  ['DaytonaValidationError', 'DaytonaBadRequestError'],
  ['DaytonaAuthorizationError', 'DaytonaForbiddenError'],
])('attaches guidance naming the surviving category to a v0 import of %s', (name, category) => {
  for (const input of [`import { ${name} } from '@daytona/sdk/v0';`, `import * as sdk from '@daytona/sdk/v0'; void sdk.${name};`]) {
    const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/input.ts', input);
    const output = suggest(source, mapping, '/').after;
    expect(output.match(/\/\/ daytona-v2:/g)).toHaveLength(1);
    expect(output).toContain(`${name} is ${category} at the root`);
  }
});
