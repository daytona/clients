/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { upgrade } from '../src/upgrade.js';

it.each([
  ['unconditional access', 'await c.v0.list();', false],
  ['rewritten unconditional access', 'await c.list();', false],
  ['if body', 'if (flag) await c.v0.list();', true],
  ['braced if body', 'if (flag) { await c.v0.list(); }', true],
  ['else body', 'if (flag) work(); else await c.v0.list();', true],
  ['both branches', 'if (flag) await c.v0.list(); else await c.v0.list();', true],
  ['logical and', 'flag && c.v0.list();', true],
  ['logical or', 'flag || c.v0.list();', true],
  ['nullish fallback', 'value ?? c.v0.list();', true],
  ['ternary arm', 'flag ? c.v0.list() : work();', true],
  ['optional property', 'c?.v0.list();', true],
  ['optional element', 'c?.["v0"].list();', true],
  ['optional call argument', 'use?.(c.v0);', true],
  ['callback', 'register(() => c.v0.list());', true],
  ['nested function', 'function later() { return c.v0.list(); }', true],
  ['loop', 'while (flag) { await c.v0.list(); }', true],
  ['for loop', 'for (const item of items) await c.v0.list();', true],
  ['switch case', 'switch (value) { case 1: await c.v0.list(); }', true],
  ['early return', 'if (flag) return; await c.v0.list();', true],
  ['logical left operand', 'c.v0 && work();', false],
  ['variable initializer', 'const v0 = c.v0;', false],
  ['return value', 'return c.v0;', false],
  ['if condition', 'if (c.v0) work();', false],
  ['if condition with else', 'if (await c.v0.list()) work(); else other();', false],
] as const)('keeps constructor guidance only when needed for %s', (_name, access, needsGuidance) => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `import { Daytona } from '@daytonaio/sdk';
async function run() { try { const c = new Daytona(); ${access} } catch {} }`);
  // When
  const result = upgrade(source, loadMapping(), { root: '/', dropApiUrl: false });
  // Then
  expect(result.items.filter(item => item.message.startsWith('SDK 2.x builds'))).toHaveLength(needsGuidance ? 1 : 0);
  expect(result.after.includes('FIXME(daytona-upgrade)')).toBe(needsGuidance);
});

it.each([
  'let c; { c = new Daytona(); c.v0.list(); }',
  'let c; { c = new Daytona(); } c.v0.list();',
] as const)('accepts guaranteed access in the construction block or an enclosing block: %s', body => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `import { Daytona } from '@daytonaio/sdk'; try { ${body} } catch {}`);
  // When
  const result = upgrade(source, loadMapping(), { root: '/', dropApiUrl: false });
  // Then
  expect(result.items).toEqual([]);
  expect(result.after).not.toContain('FIXME(daytona-upgrade)');
});

it.each([
  'let c; { c = new Daytona(); if (flag) return; } c.v0.list();',
  'let c; while (flag) { c = new Daytona(); } c.v0.list();',
] as const)('retains guidance when control flow can bypass the enclosing-block access: %s', body => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `import { Daytona } from '@daytonaio/sdk'; function run() { try { ${body} } catch {} }`);
  // When
  const result = upgrade(source, loadMapping(), { root: '/', dropApiUrl: false });
  // Then
  expect(result.items.filter(item => item.message.startsWith('SDK 2.x builds'))).toHaveLength(1);
});
