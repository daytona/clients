/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { Project } from 'ts-morph';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['import', 'import type', 'export', 'export type'].flatMap(keyword => ['\n', '\r\n'].map(newline => ({ keyword, newline }))))(
  'keeps specifier comments with their names when splitting $keyword with %j', async ({ keyword, newline }) => {
    // Given
    const before = [
      `${keyword} {`,
      '  Daytona, // the client',
      '  /* the sandbox */ Sandbox,',
      '  Image as BuildImage, // images',
      '  // before closing',
      "} from '@daytonaio/sdk';", '',
    ].join(newline);
    const root = temporaryProject({ 'app.ts': before });
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    const text = contents(root)['app.ts'] ?? '';
    const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.ts', text);
    const declarations = source.getStatements();
    expect(declarations.map(declaration => declaration.getText())).toEqual([
      [`${keyword} {`, '  Daytona, // the client', "} from '@daytona/sdk';"].join(newline),
      [`${keyword} {`, '  /* the sandbox */ Sandbox,', '  Image as BuildImage, // images',
        '  // before closing', "} from '@daytona/sdk/v0';"].join(newline),
    ]);
  },
);

it.each([
  'Daytona /* client */, /* sandbox */ Sandbox',
  '/* client */ Daytona, Sandbox /* sandbox */',
  'Daytona, // client\n Sandbox // sandbox\n',
])('retains each inline comment exactly once when splitting %s', async names => {
  // Given
  const root = temporaryProject({ 'app.ts': `import { ${names} } from '@daytonaio/sdk';\n` });
  // When
  await invoke(root);
  // Then
  const text = contents(root)['app.ts'] ?? '';
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.ts', text);
  const [kept, moved] = source.getImportDeclarations();
  expect(kept?.getText()).toMatch(/(?:\/\* client \*\/|\/\/ client)/u);
  expect(kept?.getText()).not.toMatch(/(?:\/\* sandbox \*\/|\/\/ sandbox)/u);
  expect(moved?.getText()).toMatch(/(?:\/\* sandbox \*\/|\/\/ sandbox)/u);
  expect(moved?.getText()).not.toMatch(/(?:\/\* client \*\/|\/\/ client)/u);
  expect(text.match(/(?:\/\* client \*\/|\/\/ client)/gu)).toHaveLength(1);
  expect(text.match(/(?:\/\* sandbox \*\/|\/\/ sandbox)/gu)).toHaveLength(1);
});

it('keeps the multiline layout when splitting an import without comments', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': "import {\n  Daytona,\n  Sandbox,\n} from '@daytonaio/sdk';\n" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['app.ts']).toBe("import {\n  Daytona,\n} from '@daytona/sdk';\nimport {\n  Sandbox,\n} from '@daytona/sdk/v0';\n");
});

it.each(['import', 'import type', 'export', 'export type'])('leaves commented %s splits byte-identical on a second run', async keyword => {
  // Given: the closing-list comment follows a kept name, not a moved one.
  const root = temporaryProject({ 'app.ts': `${keyword} {\n  Sandbox, /* moved */\n  Daytona, // kept\n  /* closing */\n} from '@daytonaio/sdk';\n` });
  await invoke(root);
  const before = contents(root);
  // When
  await invoke(root);
  // Then
  expect(contents(root)).toEqual(before);
});

it('preserves per-specifier type modifiers with their comments', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': "import {\n  type Daytona, // kept\n  /* moved */ type Sandbox,\n} from '@daytonaio/sdk';\n" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['app.ts']).toBe("import {\n  type Daytona, // kept\n} from '@daytona/sdk';\nimport {\n  /* moved */ type Sandbox,\n} from '@daytona/sdk/v0';\n");
});

it('preserves comments when splitting a CommonJS destructuring import', async () => {
  // Given
  const root = temporaryProject({ 'app.js': "const {\n  Daytona, // kept\n  /* moved */ Sandbox,\n} = require('@daytonaio/sdk');\n" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['app.js']).toBe("const {\n  Daytona, // kept\n} = require('@daytona/sdk');\nconst {\n  /* moved */ Sandbox,\n} = require('@daytona/sdk/v0');\n");
});

it('keeps comment-free one-line imports unchanged in style when a name contains comment delimiters', async () => {
  // Given
  const root = temporaryProject({ 'app.ts': 'import { Daytona, "Sandbox/*tag*/" as Sandbox } from "@daytonaio/sdk";\n' });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['app.ts']).toBe('import { Daytona } from "@daytona/sdk";\nimport { "Sandbox/*tag*/" as Sandbox } from "@daytona/sdk/v0";\n');
});

it.each(['@ts-ignore', '@ts-expect-error', 'eslint-disable-next-line no-unused-vars',
  'biome-ignore lint/correctness/noUnusedImports: example', 'prettier-ignore'].flatMap(directive =>
  ['import', 'import type', 'export', 'export type', 'require'].flatMap(keyword =>
    [false, true].map(reversed => ({ directive, keyword, reversed })))))(
  'keeps next-line $directive with the following entry in $keyword (reversed=$reversed)',
  async ({ directive, keyword, reversed }) => {
    // Given
    const first = reversed ? 'Sandbox' : 'Daytona';
    const second = reversed ? 'Daytona' : 'Sandbox';
    const names = `\n  ${first}, /* trailing-marker */ // ${directive}\n  ${second},\n`;
    const before = keyword === 'require' ? `const {${names}} = require('@daytonaio/sdk');\n`
      : `${keyword} {${names}} from '@daytonaio/sdk';\n`;
    const root = temporaryProject({ 'app.ts': before });
    // When
    await invoke(root);
    // Then
    const text = contents(root)['app.ts'] ?? '';
    expect(text.split(directive)).toHaveLength(2);
    expect(text.split('trailing-marker')).toHaveLength(2);
    expect(text).toContain(`// ${directive}\n  ${second},`);
    expect(text).toContain(`${first}, /* trailing-marker */`);
  },
);

it.each(['import', 'export'])('copies all trailing comments when splitting an %s', async keyword => {
  // Given
  const root = temporaryProject({ 'app.ts': `${keyword} { Daytona, Sandbox } from '@daytonaio/sdk'; /* first-marker */ /* second-marker */ // final-marker\n` });
  // When
  await invoke(root);
  // Then
  const text = contents(root)['app.ts'] ?? '';
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.ts', text);
  expect(source.getStatements()).toHaveLength(2);
  for (const marker of ['first-marker', 'second-marker', 'final-marker']) {
    expect(text.split(marker)).toHaveLength(3);
    for (const declaration of source.getStatements()) {
      expect(declaration.getTrailingCommentRanges().map(comment => comment.getText()).join(' ')).toContain(marker);
    }
  }
});
