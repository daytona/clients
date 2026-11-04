/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each([
  { quote: "'", semicolon: '' }, { quote: '"', semicolon: '' },
  { quote: "'", semicolon: ';' }, { quote: '"', semicolon: ';' },
])('follows the Daytona import style when splitting named imports ($quote, $semicolon)', async ({ quote, semicolon }) => {
  // Given: other imports deliberately use the opposite style.
  const otherQuote = quote === "'" ? '"' : "'";
  const otherSemicolon = semicolon === ';' ? '' : ';';
  const others = ['one', 'two'].map(name => `import ${name} from ${otherQuote}${name}${otherQuote}${otherSemicolon}\n`).join('');
  const root = temporaryProject({ 'index.ts': `${others}import { Daytona, Sandbox } from ${quote}@daytonaio/sdk${quote}${semicolon}\n` });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['index.ts']).toBe(`${others}import { Daytona } from ${quote}@daytona/sdk${quote}${semicolon}\nimport { Sandbox } from ${quote}@daytona/sdk/v0${quote}${semicolon}\n`);
});
it('follows the Daytona import style when inserting a namespace binding', async () => {
  // Given
  const root = temporaryProject({ 'index.ts': 'import * as sdk from "@daytonaio/sdk"\r\nconst image = sdk.Image;\r\n' });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toBe('import * as sdk from "@daytona/sdk"\r\nimport * as sdkV0 from "@daytona/sdk/v0"\r\nconst image = sdkV0.Image;\r\n');
});
it('uses the majority import style when a split re-export has no Daytona import', async () => {
  // Given
  const imports = 'import one from "one"\r\nimport two from "two"\r\nimport three from \'three\';\r\n';
  const root = temporaryProject({ 'index.ts': `${imports}export { Daytona, Sandbox } from '@daytonaio/sdk';\r\n` });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.ts']).toBe(`${imports}export { Daytona } from '@daytona/sdk';\r\nexport { Sandbox } from "@daytona/sdk/v0"\r\n`);
});
it('preserves the require declaration style when inserting a namespace binding', async () => {
  // Given
  const root = temporaryProject({ 'index.js': "const sdk = require('@daytonaio/sdk')\nconst label = sdk.Image\n" });
  // When
  await invoke(root);
  // Then
  expect(contents(root)['index.js']).toBe("const sdk = require('@daytona/sdk'), sdkV0 = require('@daytona/sdk/v0')\nconst label = sdkV0.Image\n");
});
