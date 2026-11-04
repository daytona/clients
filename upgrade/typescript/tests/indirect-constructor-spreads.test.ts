/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const forms = [
  'Reflect.construct(Daytona, [ARGS]);',
  'Daytona.bind(null, ARGS);',
] as const;

it.each(forms.flatMap(form => [
  '...maybeArgs, CONFIG',
  '...maybeArgs, ...otherArgs, CONFIG',
  '...[...maybeArgs], CONFIG',
  '...[...maybeArgs, ...[]], CONFIG',
  '...maybeArgs, ...[CONFIG]',
  '...[], ...maybeArgs, CONFIG',
].flatMap(args => ['apiUrl', 'serverUrl'].flatMap(field => [false, true].map(drop => ({ form, args, field, drop }))))))(
  'refuses a possible custom first argument when $form uses $args, $field, drop=$drop', async ({ form, args, field, drop }) => {
    // Given
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
      'app.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
        'declare const maybeArgs: unknown[]; declare const otherArgs: unknown[];\n' +
        form.replace('ARGS', args.replace('CONFIG', `{ ${field}: 'https://custom.example' }`)),
    });
    installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code, result.output).toBe(2);
    expect(JSON.parse(result.output)).toMatchObject({ refused: { reason: 'custom-api-url' } });
    expect(contents(root)).toEqual(before);
  },
);

it.each(forms.flatMap(form => [
  '...maybeArgs',
  '...maybeArgs, { apiUrl: "https://app.daytona.io/api" }',
  '...maybeArgs, ...otherArgs, { serverUrl: "https://app.daytona.io/api" }',
].flatMap(args => [false, true].map(drop => ({ form, args, drop }))))) (
  'preserves unresolved spread handling when $form uses $args, drop=$drop', async ({ form, args, drop }) => {
    // Given
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
      'app.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
        'declare const maybeArgs: unknown[]; declare const otherArgs: unknown[];\n' + form.replace('ARGS', args),
    });
    installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    expect(result.code, result.output).toBe(drop ? 0 : 2);
    expect(JSON.parse(result.output)).toMatchObject({
      items: expect.arrayContaining([expect.objectContaining({ kind: drop ? 'config-field' : 'custom-api-url' })]),
    });
    if (drop) {
      expect(contents(root)['app.ts']).not.toMatch(/apiUrl|serverUrl/);
      expect(contents(root)['package.json']).toContain('@daytona/sdk');
    } else expect(contents(root)).toEqual(before);
  },
);

it.each(forms.flatMap(form => [
  '...[], CONFIG',
  '...[cfg]',
  '...args',
  '...[...[], cfg]',
  '...[...maybeArgs, cfg]',
].flatMap(args => ['https://custom.example', 'https://app.daytona.io/api'].flatMap(url => [false, true].map(drop => ({ form, args, url, drop })))))) (
  'resolves literal spreads when $form uses $args with $url, drop=$drop', async ({ form, args, url, drop }) => {
    // Given
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
      'app.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
        `declare const maybeArgs: unknown[]; const cfg = { apiUrl: '${url}' }; const args = [cfg] as const;\n` +
        form.replace('ARGS', args.replace('CONFIG', 'cfg')),
    });
    installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json', ...(drop ? ['--drop-api-url'] : [])]);
    // Then
    const refused = url === 'https://custom.example' || !drop && args.includes('maybeArgs');
    expect(result.code, result.output).toBe(refused ? 2 : 0);
    if (refused) expect(contents(root)).toEqual(before);
    else expect(contents(root)['app.ts']).not.toContain('apiUrl');
  },
);

it.each(forms.flatMap(form => [
  '{}, ...maybeArgs, { apiUrl: "https://custom.example" }',
  '...[{}], { apiUrl: "https://custom.example" }',
  '...maybeArgs, {}, { apiUrl: "https://custom.example" }',
  '...[...maybeArgs, {}], { apiUrl: "https://custom.example" }',
  '...maybeArgs, ...[{}], { apiUrl: "https://custom.example" }',
].map(args => ({ form, args })))) (
  'ignores later arguments after a definite first slot when $form uses $args', async ({ form, args }) => {
    // Given
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
      'app.ts': "import { Daytona } from '@daytonaio/sdk';\n" +
        'declare const maybeArgs: unknown[];\n' + form.replace('ARGS', args),
    });
    installFakeSdk(root);
    // When
    const result = await invoke(root, ['--json', '--drop-api-url']);
    // Then
    expect(result.code, result.output).toBe(0);
    expect(contents(root)['app.ts']).toContain('https://custom.example');
  },
);
