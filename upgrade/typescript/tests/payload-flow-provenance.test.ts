/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { ExportProvenance } from '../src/export-provenance.js';
import { loadMapping } from '../src/mapping.js';
import { callbackCases, payloadCases } from './payload-flow-cases.js';

const sdk = "import { Daytona } from '@daytona/sdk';\n";
const projections = [
  { name: 'member', code: (payload: string) => `const result = (${payload}).client;` },
  { name: 'call argument', code: (payload: string) => `function read(ctx) { return ctx.client; } const result = read(${payload});` },
  { name: 'destructured factory return', code: (payload: string) => `function make() { return ${payload}; } const { client: result } = make();` },
] as const;

it.each(payloadCases.flatMap(testCase => projections.map(projection => ({ ...testCase, projection }))))(
  'keeps $projection.name provenance review-only when $name', ({ payload, origin, linked, projection }) => {
    // Given an unresolved receiver projected from a possibly overwritten payload.
    const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
    const source = project.createSourceFile('/use.js', `${sdk}const replacement = { client: {} }; ${projection.code(payload)}`);
    const declaration = source.getVariableDeclarationOrThrow(declaration => declaration.getName().includes('result'));
    const binding = declaration.getFirstDescendantByKind(SyntaxKind.BindingElement);
    const trace = new ExportProvenance(loadMapping(), []);
    // When the selected value's origin is traced, independently of SDK types.
    const actual = trace.client(binding ?? declaration.getInitializerOrThrow());
    // Then literal payloads provide linked evidence, never a client proof.
    expect({ origin: actual, linked: trace.linkedDaytona }).toEqual({ origin, linked });
  });

it.each(callbackCases)('traces the called parameter return when $name', ({ callback, origin, linked }) => {
  // Given a higher-order factory with a callback whose return determines the value.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `${sdk}import axios from 'axios';
    function invoke(factory) { return factory(); }
    const make = () => new Daytona();
    const result = invoke(${callback});`);
  const trace = new ExportProvenance(loadMapping(), []);
  // When the factory result is traced.
  const actual = trace.client(source.getVariableDeclarationOrThrow('result').getInitializerOrThrow());
  // Then callback returns, not function objects, supply the provenance.
  expect({ origin: actual, linked: trace.linkedDaytona }).toEqual({ origin, linked });
});

it.each([
  { name: 'unrelated invocation', code: 'invoke(() => new Daytona()); const result = invoke(opaque);', linked: false },
  { name: 'unresolved callable', code: 'const result = invoke(opaque);', linked: false },
  { name: 'default callback', code: 'const result = withDefault();', linked: true },
  { name: 'nested forwarding', code: 'const result = forward(() => new Daytona());', linked: true },
])('keeps callback scope precise when $name', ({ code, linked }) => {
  // Given multiple invocations and an opaque callback boundary.
  const project = new Project({ useInMemoryFileSystem: true, compilerOptions: { allowJs: true } });
  const source = project.createSourceFile('/use.js', `${sdk}
    function invoke(factory) { return factory(); }
    function withDefault(factory = () => new Daytona()) { return factory(); }
    function forward(callback) { return invoke(callback); }
    ${code}`);
  const trace = new ExportProvenance(loadMapping(), []);
  // When the chosen invocation's result is traced.
  const origin = trace.client(source.getVariableDeclarationOrThrow('result').getInitializerOrThrow());
  // Then only the chosen invocation supplies evidence, without a rewrite proof.
  expect({ origin, linked: trace.linkedDaytona }).toEqual({ origin: 'unknown', linked });
});
