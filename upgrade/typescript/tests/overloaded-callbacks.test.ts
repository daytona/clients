/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { CallbackCallIndex } from '../src/callback-call-index.js';
import type { Report } from '../src/model.js';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

const flows = [
  { name: 'supplied callback', signature: 'cb: (c: any) => void', parameters: 'cb: any',
    body: 'cb(new Daytona());', arguments: 'c => c.create()' },
  { name: 'forwarded client', signature: 'value: any, cb: (c: any) => void', parameters: 'value: any, cb: any',
    body: 'cb(value);', arguments: 'new Daytona(), c => c.create()' },
  { name: 'direct client', signature: 'value: any', parameters: 'value: any',
    body: 'value.create();', arguments: 'new Daytona()' },
] as const;
const wrappers = [
  { name: 'function', member: 'function run', before: '', after: '', call: 'run' },
  { name: 'method', member: 'run', before: 'class Run {', after: '}', call: 'new Run().run' },
  { name: 'static method', member: 'static run', before: 'class Run {', after: '}', call: 'Run.run' },
  { name: 'constructor', member: 'constructor', before: 'class Run {', after: '}', call: 'new Run' },
] as const;
const cases = wrappers.flatMap(wrapper => flows.flatMap(flow => [false, true].map(overloaded => ({
  name: `${wrapper.name}, ${flow.name}, overloaded=${overloaded}`,
  source: `${wrapper.before}
${overloaded ? `${wrapper.member}(${flow.signature});` : ''}
${wrapper.member}(${flow.parameters}) { ${flow.body} }
${wrapper.after}
${wrapper.call}(${flow.arguments});`,
}))));

it.each(cases)('retains review-only callback evidence for $name', async ({ source }) => {
  // Given an untyped implementation, with or without a separate overload signature.
  const root = temporaryProject({
    'app.ts': `import { Daytona } from '@daytonaio/sdk';\n${source}\n`,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
  });
  installFakeSdk(root);
  // When the real CLI analyzes calls in strict mode.
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then the implementation's receiver requires review, never a guessed rewrite.
  const report: Report = JSON.parse(result.output);
  expect({ code: result.code, calls: report.items.filter(item => item.kind === 'fixme')
    .map(item => ({ kind: item.kind, file: item.file })) })
    .toEqual({ code: 1, calls: [{ kind: 'fixme', file: 'app.ts' }] });
});

it.each(wrappers)('keeps overloaded $name callback evidence invocation-specific', async wrapper => {
  // Given an SDK argument in a different invocation from the callback under review.
  const root = temporaryProject({
    'app.ts': `import { Daytona } from '@daytonaio/sdk';
import axios from 'axios';
${wrapper.before}
${wrapper.member}(value: any, cb: (c: any) => void);
${wrapper.member}(value: any, cb: any) { cb(value); }
${wrapper.after}
${wrapper.call}(new Daytona(), () => {});
${wrapper.call}(axios.create(), c => c.create());`,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
  });
  installFakeSdk(root);
  // When strict migration resolves the external client's callback.
  const result = await invoke(root, ['--strict', '--json', '--force', '--yes']);
  // Then broad candidate links do not invent SDK provenance across invocations.
  const report: Report = JSON.parse(result.output);
  expect({ code: result.code, calls: report.items.filter(item => item.kind === 'fixme') })
    .toEqual({ code: 0, calls: [] });
  // And the external client's callback call is left exactly as written.
  const source = readFileSync(join(root, 'app.ts'), 'utf8');
  expect(source).toContain('c => c.create()');
  expect(source).not.toContain('c.v0.create');
});

it.each([
  { name: 'signature rest', signature: 'prefix: string, ...callbacks: ((c: any) => void)[]',
    parameters: 'prefix: any, first: any, second: any', body: 'second(new Daytona());' },
  { name: 'implementation rest', signature: 'prefix: string, first: (c: any) => void, second: (c: any) => void',
    parameters: 'prefix: any, ...callbacks: any[]', body: "callbacks['1'](new Daytona());" },
  { name: 'both rest', signature: 'prefix: string, ...callbacks: ((c: any) => void)[]',
    parameters: 'prefix: any, ...callbacks: any[]', body: "callbacks['1'](new Daytona());" },
])('indexes later callbacks through $name slots', ({ signature, parameters, body }) => {
  // Given a callback beyond the rest parameter's first argument position.
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('/use.ts', `function run(${signature}): void;
function run(${parameters}) { ${body} }
run('prefix', () => {}, c => c.create());`);
  const callback = source.getDescendantsOfKind(SyntaxKind.ArrowFunction)[1];
  const invocation = source.getDescendantsOfKind(SyntaxKind.CallExpression).find(call =>
    call.getExpression().getText() === 'second' || call.getExpression().getText() === "callbacks['1']");
  assert(callback);
  assert(invocation);
  // When the candidate filter follows the supplied callback into the wrapper.
  const candidates = new CallbackCallIndex(project).candidates(callback, () => true);
  // Then the body's invocation stays reachable; exact resolution remains separate.
  expect(candidates).toContain(invocation);
});
