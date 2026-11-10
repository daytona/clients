/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import { invoke, temporaryProject } from './helpers.js';

// A 0.x lifecycle call that used the default timeout keeps v2's default, and
// the suggestion names no variable the call did not have; one that passed a
// timeout keeps it, by its own source. The Python tool renders the same forms
// (tests/test_suggestions.py).
it.each([
  ['box.start()', 'sandbox.start', 'box.start()'],
  ['box.stop()', 'sandbox.stop', 'box.stop()'],
  ['box.delete()', 'sandbox.delete', 'box.delete()'],
  ['box.pause()', 'sandbox.pause', 'box.pause()'],
  ['box.waitUntilStarted()', 'sandbox.waitUntilStarted', "box.wait({ until: 'started' })"],
  ['box.fork()', 'sandbox.fork', 'box.fork({ memory: false })'],
  ['d.v0.delete(box)', 'client.delete', 'd.v2.delete(sandbox)'],
  ['box.start(120)', 'sandbox.start', 'box.start({ timeoutSeconds: 120 })'],
  ['box.pause(5)', 'sandbox.pause', 'box.pause({ timeoutSeconds: 5 })'],
  ["box.fork({ name: 'copy' }, 30)", 'sandbox.fork', "box.fork({ ...{ name: 'copy' }, memory: false, waitSeconds: 30 })"],
  // A numeric literal is computed; a variable keeps the expression.
  ['box.fork(undefined, 0)', 'sandbox.fork', 'box.fork({ memory: false, waitSeconds: 30 * 86_400 })'],
  ['box.fork(undefined, budget)', 'sandbox.fork', 'box.fork({ memory: false, waitSeconds: budget === 0 ? 30 * 86_400 : budget })'],
  // An expression is parenthesized where the form computes with it.
  ['box.fork(undefined, budget > 0 ? budget : 60)', 'sandbox.fork', 'box.fork({ memory: false, waitSeconds: (budget > 0 ? budget : 60) === 0 ? 30 * 86_400 : (budget > 0 ? budget : 60) })'],
  ['box.fork(undefined, budget || 60)', 'sandbox.fork', 'box.fork({ memory: false, waitSeconds: (budget || 60) === 0 ? 30 * 86_400 : (budget || 60) })'],
  ['box.pause(budget || 60)', 'sandbox.pause', 'box.pause({ timeoutSeconds: budget || 60 })'],
  // Keyboard calls carry the call's own arguments; a delay is converted from
  // milliseconds, parenthesized where it is an expression.
  ["box.computerUse.keyboard.type('hi')", 'sandbox.computerUse.keyboard.type', "box.computerUse.keyboard.type({ text: 'hi' })"],
  ['box.computerUse.keyboard.type(String(budget), 50)', 'sandbox.computerUse.keyboard.type', 'box.computerUse.keyboard.type({ text: String(budget), delaySeconds: 50 / 1000 })'],
  ['box.computerUse.keyboard.type(String(budget), budget + 5)', 'sandbox.computerUse.keyboard.type', 'box.computerUse.keyboard.type({ text: String(budget), delaySeconds: (budget + 5) === undefined ? undefined : (budget + 5) / 1000 })'],
  ["box.computerUse.keyboard.press('a', ['ctrl'])", 'sandbox.computerUse.keyboard.press', "box.computerUse.keyboard.key({ key: 'a', modifiers: ['ctrl'] })"],
  ["box.computerUse.keyboard.hotkey('ctrl+c')", 'sandbox.computerUse.keyboard.hotkey', "box.computerUse.keyboard.hotkey({ keys: 'ctrl+c' })"],
  // `sandbox` in a client form stands for the 0.x call's sandbox, as the
  // guide says of every identifier but `{client}` / `{sandbox}`.
  ['d.v0.stop(box)', 'client.stop', 'd.v2.stop(sandbox)'],
  ['d.v0.stop(box, 30)', 'client.stop', 'd.v2.stop(sandbox, { timeoutSeconds: 30 })'],
  ['d.v0.delete(box, 30)', 'client.delete', 'd.v2.delete(sandbox, { timeoutSeconds: 30 })'],
])('suggests %s with only the timeout the call passed', async (call, id, expected) => {
  // Given
  const root = temporaryProject({ 'index.ts': `import { Daytona } from '@daytona/sdk';\nconst d = new Daytona();\nconst box = await d.v0.create();\nconst budget = Number(process.env.BUDGET);\nawait ${call};\n` });
  // When
  const result = await invoke(root, ['--suggest-v2', '--json'], loadMapping());
  // Then
  const items = (JSON.parse(result.output) as { items: { suggestion?: { id: string; v2: string | null } }[] }).items;
  expect(items.find((item) => item.suggestion?.id === id)?.suggestion?.v2).toBe(expected);
});
