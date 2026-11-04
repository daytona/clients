/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['import', 'export'].flatMap(keyword => ['\n', '\r\n'].flatMap(newline => [
  '// eslint-disable-next-line',
  '// eslint-disable-next-line custom/rule -- keep this reason',
  '/* eslint-disable-next-line custom/rule */',
].map(comment => ({ keyword, newline, comment })))))(
  'duplicates a preceding $comment when splitting $keyword with newline %j', async ({ keyword, newline, comment }) => {
    // Given
    const root = temporaryProject({ 'app.ts': `${comment}${newline}${keyword} { Daytona, Sandbox } from '@daytonaio/sdk';${newline}` });
    // When
    const result = await invoke(root);
    // Then
    expect(result.code).toBe(0);
    expect(contents(root)['app.ts']).toBe(`${comment}${newline}${keyword} { Daytona } from '@daytona/sdk';${newline}${comment}${newline}${keyword} { Sandbox } from '@daytona/sdk/v0';${newline}`);
  },
);

it.each(['import', 'export'].flatMap(keyword => [
  '// resource ownership note',
  '/* resource ownership note */',
  '// eslint-disable-line custom/rule -- keep this reason',
].map(comment => ({ keyword, comment }))))('copies trailing $comment to both split $keyword declarations', async ({ keyword, comment }) => {
  // Given
  const root = temporaryProject({ 'app.ts': `${keyword} { Daytona, Sandbox } from '@daytonaio/sdk'; ${comment}\n` });
  // When
  const result = await invoke(root);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)['app.ts']).toBe(`${keyword} { Daytona } from '@daytona/sdk'; ${comment}\n${keyword} { Sandbox } from '@daytona/sdk/v0'; ${comment}\n`);
});

it.each(['// explanation', '// eslint-disable custom/rule', '// eslint-disable-next-line custom/rule\n\n'])(
  'keeps unrelated leading trivia singular when it is %s', async comment => {
    // Given
    const root = temporaryProject({ 'app.ts': `${comment}\nimport { Daytona, Sandbox } from '@daytonaio/sdk';\n` });
    // When
    await invoke(root);
    // Then
    expect(contents(root)['app.ts']).toBe(`${comment}\nimport { Daytona } from '@daytona/sdk';\nimport { Sandbox } from '@daytona/sdk/v0';\n`);
  },
);
