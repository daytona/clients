/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const receivers = ["require('@daytonaio/sdk')", "(await import('@daytonaio/sdk'))"] as const;
const names = ['Sandbox', 'SandboxState', 'Daytona', 'DaytonaError', 'default'] as const;
const accesses = names.flatMap(name => [`.${name}`, `['${name}']`, `[\`${name}\`]`]);

it.each(receivers.flatMap(receiver => accesses.map(access => ({ receiver, access }))))(
  'routes a direct module property when using $receiver$access', async ({ receiver, access }) => {
    // Given
    const root = temporaryProject({ 'index.ts': `const value = ${receiver}${access};` });
    const module = /Sandbox|DaytonaError/.test(access) ? '@daytona/sdk/v0' : '@daytona/sdk';
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(contents(root)['index.ts']).toBe(`const value = ${receiver.replace('@daytonaio/sdk', module)}${access};`);
    const report: Report = JSON.parse(result.output);
    expect(report.items.filter(item => item.kind === 'private-import')).toHaveLength(access.includes('default') ? 1 : 0);
  });

it.each(receivers.flatMap(receiver => ['[key]', ''].map(access => ({ receiver, access }))))(
  'keeps root routing with review when using $receiver$access', async ({ receiver, access }) => {
    // Given
    const root = temporaryProject({ 'index.ts': `consume(${receiver}${access});` });
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(contents(root)['index.ts']).toContain(receiver.replace('@daytonaio/sdk', '@daytona/sdk'));
    const report: Report = JSON.parse(result.output);
    expect(report.items).toContainEqual(expect.objectContaining({ kind: 'dynamic-import' }));
  });

it.each(receivers)('does not edit migrated direct imports when using %s', async receiver => {
  // Given
  const root = temporaryProject({ 'index.ts': `const value = ${receiver}.Sandbox;` });
  await invoke(root);
  const before = contents(root);
  // When
  const result = await invoke(root, ['--json']);
  // Then
  expect(result.code).toBe(0);
  expect(contents(root)).toEqual(before);
  const report: Report = JSON.parse(result.output);
  expect(report.changedFiles).toEqual([]);
});

it.each(receivers.flatMap(receiver => ['.Daytona', "['Daytona']"].flatMap(access =>
  [false, true].map(installed => ({ receiver, access, installed })))))(
  'migrates a direct Daytona constructor when using $receiver$access, installed=$installed', async ({ receiver, access, installed }) => {
    // Given
    const root = temporaryProject({ 'index.ts': `const Daytona = ${receiver}${access};\nconst d = new Daytona({ apiKey: 'k' });\nd.create();` });
    if (installed) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(0);
    expect(contents(root)['index.ts']).toContain('d.v0.create()');
  });

it.each(receivers.flatMap(receiver => ['.Daytona', "['Daytona']"].flatMap(access =>
  [false, true].map(installed => ({ receiver, access, installed })))))(
  'refuses a direct constructor custom URL when using $receiver$access, installed=$installed', async ({ receiver, access, installed }) => {
    // Given
    const root = temporaryProject({ 'index.ts': `const Daytona = ${receiver}${access};\nconst d = new Daytona({ apiUrl: 'https://custom.example' });\nd.create();` });
    if (installed) installFakeSdk(root);
    const before = contents(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then
    expect(result.code).toBe(2);
    const report: Report = JSON.parse(result.output);
    expect(report.refused?.reason).toBe('custom-api-url');
    expect(contents(root)).toEqual(before);
  });
