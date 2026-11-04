/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { ExportProvenance } from '../src/export-provenance.js';
import { loadMapping } from '../src/mapping.js';

it.each([
  { name: 'partial Daytona proof', value: 'new Daytona()', callable: false },
  { name: 'default non-Daytona proof', value: '() => ({})', callable: true },
])('retains an incomplete origin when the work budget expires instead of a $name', ({ value, callable }) => {
  // Given a known argument/default followed by enough candidate calls to exhaust
  // the whole search. Neither partial results nor defaults may establish origin.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';
function consume(value = ${value}) { return value; }
consume(${value});
${'consume(opaque);\n'.repeat(6_000)}`);
  const parameter = source.getFunctionOrThrow('consume').getParameters()[0];
  assert(parameter);
  const analyzer = new ExportProvenance(loadMapping(), []);
  // When the complete parameter origin is requested, not just its first supplier.
  const origin = analyzer.parameterOrigin(parameter, new Set(), callable);
  // Then exhaustion cannot become either a partial proof or a default proof.
  expect(origin).toBe('incomplete');
});

it.each([false, true])('keeps cached parameter origins invocation-specific when reversed=%s', reversed => {
  // Given two invocations of one factory whose supplied values disagree.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';
function identity(value) { return value; }
identity(new Daytona());
identity({});`);
  const calls = source.getDescendantsOfKind(SyntaxKind.CallExpression);
  const ordered = reversed ? [...calls].reverse() : calls;
  const analyzer = new ExportProvenance(loadMapping(), []);
  // When one analyzer resolves both invocations, sharing the project memo.
  const origins = ordered.map(call => analyzer.client(call));
  // Then neither the first argument nor its call stack contaminates the second.
  expect(origins).toEqual(reversed ? ['not-daytona', 'daytona'] : ['daytona', 'not-daytona']);
});

it('marks recursion-limited callers incomplete instead of proving a factory default', () => {
  // Given a Daytona default with a non-Daytona caller hidden behind a long alias chain.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';
function run(factory = () => new Daytona()) { return factory(); }
${Array.from({ length: 40 }, (_, index) => `const alias${index} = ${index ? `alias${index - 1}` : 'run'};`).join('\n')}
alias39(() => ({}));`);
  const parameter = source.getFunctionOrThrow('run').getParameters()[0];
  assert(parameter);
  const analyzer = new ExportProvenance(loadMapping(), []);
  // When callable origin analysis reaches its recursion cap.
  const origin = analyzer.parameterOrigin(parameter, new Set(), true);
  // Then the default is not a proof: the origin records incomplete analysis.
  expect(origin).toBe('incomplete');
});

it.each([
  'function run(client) { client.create(); }\nrun(new Daytona());',
  'class Run { constructor(client) { client.create(); } }\nnew Run(new Daytona());',
  'function run(cb) { cb(new Daytona()); }\nrun(client => client.create());',
])('completes linked parameter analysis despite unrelated calls when %s', body => {
  // Given twenty thousand calls that cannot target the parameter's owner.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `import { Daytona } from '@daytona/sdk';\n${body}`);
  project.createSourceFile('/noise.js', `function unrelated() {}\n${'unrelated();\n'.repeat(20_000)}`);
  const parameter = source.getDescendantsOfKind(SyntaxKind.Parameter).find(node => node.getName() === 'client');
  assert(parameter);
  const analyzer = new ExportProvenance(loadMapping(), []);
  // When the complete origin is requested (not merely whether review is needed).
  const origin = analyzer.parameterOrigin(parameter, new Set());
  // Then unrelated calls consumed no search budget; this is complete evidence.
  expect(origin).toBe('daytona');
});
