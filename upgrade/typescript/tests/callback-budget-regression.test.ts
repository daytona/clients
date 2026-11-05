/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { performance } from 'node:perf_hooks';
import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';
import { expectWithinBudget, testTimeout } from './budget.js';

const shapes = [
  { name: 'function parameter', source: 'function run(client) { client.create(); }\nrun(new Daytona());' },
  { name: 'constructor parameter', source: 'class Run { constructor(client) { client.create(); } }\nnew Run(new Daytona());' },
  { name: 'callback parameter', source: 'function run(cb) { cb(new Daytona()); }\nrun(client => client.create());' },
] as const;
const cases = [1_000, 5_000, 20_000].flatMap(count => ['js', 'ts'].flatMap(extension =>
  shapes.map(shape => ({ ...shape, count, extension }))));

it.each(cases)('retains review for $name in .$extension with $count unrelated calls', async ({ source, count, extension }) => {
  // Given an untyped parameter with linked SDK evidence and unrelated work in another file.
  const root = temporaryProject({
    [`app.${extension}`]: `import { Daytona } from '@daytonaio/sdk';\n${source}\n`,
    [`noise.${extension}`]: `function unrelated() {}\n${'unrelated();\n'.repeat(count)}`,
    'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
  });
  installFakeSdk(root);
  // When the real CLI analyzes the project under strict mode.
  const start = performance.now();
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  const elapsed = performance.now() - start;
  console.info(`callback candidates: ${count} unrelated calls, ${(elapsed / 1000).toFixed(3)} s`);
  // Then linked calls remain review-only at every size, within a fixed runtime budget.
  const report: Report = JSON.parse(result.output);
  expect({ code: result.code, items: report.items.filter(item => item.kind === 'fixme').map(item => item.file) })
    .toEqual({ code: 1, items: [`app.${extension}`] });
  expectWithinBudget(elapsed, 10_000);
}, testTimeout(20_000));

it.each([
  { name: 'parameter', body: 'client.create();', parameter: 'client = new Daytona()', argument: '{ create() {} }' },
  { name: 'member', body: 'opts.client.create();', parameter: 'opts', argument: '{ client: { create() {} } }' },
  { name: 'destructuring', body: 'const { client } = opts; client.create();', parameter: 'opts', argument: '{ client: { create() {} } }' },
])('requires review for $name when candidate calls genuinely exhaust the budget', async ({ body, parameter, argument }) => {
  // Given enough actual callers to exhaust analysis after a non-Daytona argument.
  const root = temporaryProject({
    'app.js': `import { Daytona } from '@daytona/sdk';
function run(${parameter}) { ${body} }
${`run(${argument});\n`.repeat(6_000)}`,
    'package.json': '{"dependencies":{"@daytona/sdk":"0.224.0"}}',
  });
  // When strict migration cannot finish the parameter search.
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then neither partial negative evidence nor the default hides the review item.
  const report: Report = JSON.parse(result.output);
  expect({ code: result.code, items: report.items.filter(item => item.kind === 'fixme').map(item => item.file) })
    .toEqual({ code: 1, items: ['app.js'] });
});

it('requires review for an uninstalled SDK constructor in a parameter default', async () => {
  // Given an untyped default with SDK identity, but no installed dependencies or callers.
  const root = temporaryProject({
    'app.js': "import { Daytona } from '@daytona/sdk';\nfunction run(client = new Daytona()) { client.create(); }\n",
    'package.json': '{"dependencies":{"@daytona/sdk":"0.224.0"}}',
  });
  // When strict migration traces the receiver.
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then the default is review evidence, never rewrite proof or a silent miss.
  const report: Report = JSON.parse(result.output);
  expect({ code: result.code, items: report.items.filter(item => item.kind === 'fixme').map(item => item.file) })
    .toEqual({ code: 1, items: ['app.js'] });
});

it('requires review when callable aliases exceed the recursion cap', async () => {
  // Given a callback search too deep to prove there are no callers.
  const aliases = Array.from({ length: 40 }, (_, index) => `const alias${index} = ${index ? `alias${index - 1}` : 'run'};`).join('\n');
  const root = temporaryProject({
    'app.js': `import { Daytona } from '@daytona/sdk';
function run(client) { client.create(); }
${aliases}
alias39(new Daytona());`,
    'package.json': '{"dependencies":{"@daytona/sdk":"0.224.0"}}',
  });
  // When analysis hits its 64-node callable recursion cap.
  const result = await invoke(root, ['--dry', '--strict', '--json']);
  // Then the incomplete search requires review instead of falling back to the default.
  const report: Report = JSON.parse(result.output);
  expect({ code: result.code, items: report.items.filter(item => item.kind === 'fixme').map(item => item.file) })
    .toEqual({ code: 1, items: ['app.js'] });
});
