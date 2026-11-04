/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { Project } from 'ts-morph';
import { expect, it } from 'vitest';
import { applyEdits } from '../src/edits.js';
import { imports } from '../src/imports.js';
import { loadMapping } from '../src/mapping.js';

it.each(['const', 'let', 'var'])('emits two %s statements for a mixed CommonJS import', keyword => {
  // Given
  const input = `${keyword} { Daytona: D, Sandbox } = require('@daytonaio/sdk');\n`;
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('app.js', input);
  // When
  const output = applyEdits(input, imports(source, loadMapping(), '/').edits);
  // Then
  const migrated = project.createSourceFile('migrated.js', output);
  expect(migrated.getVariableStatements().map(statement => statement.getDeclarationKind())).toEqual([keyword, keyword]);
  expect(migrated.getVariableStatements().map(statement => statement.getDeclarations().length)).toEqual([1, 1]);
  expect(output).toContain(`${keyword} { Sandbox } = require('@daytona/sdk/v0')`);
});

it.each(['const', 'let', 'var'])('preserves multi-declaration evaluation order when splitting %s imports', keyword => {
  // Given
  const input = `${keyword} before = mark('before'), { Daytona, Sandbox } = require('@daytonaio/sdk'), after = mark('after');\n`;
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.js', input);
  const events: string[] = [];
  // When
  const output = applyEdits(input, imports(source, loadMapping(), '/').edits);
  runInNewContext(output, {
    mark: (name: string) => events.push(name),
    require: (name: string) => { events.push(name); return { Daytona: class {}, Sandbox: class {} }; },
  });
  // Then
  expect(events).toEqual(['before', '@daytona/sdk', '@daytona/sdk/v0', 'after']);
});

it.each(['\n', '\r\n'])('preserves indentation and semicolon-free style with %j', newline => {
  // Given
  const input = ['function load() {', '  let { Daytona, Sandbox } = require("@daytonaio/sdk")', '}', ''].join(newline);
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.js', input);
  // When
  const output = applyEdits(input, imports(source, loadMapping(), '/').edits);
  // Then
  expect(output).toBe(['function load() {', '  let { Daytona } = require("@daytona/sdk")',
    '  let { Sandbox } = require("@daytona/sdk/v0")', '}', ''].join(newline));
});

it('splits repeated mixed imports without moving surrounding initializers', () => {
  // Given
  const input = "var before = mark('before'), { Daytona: D, Sandbox: S } = require('@daytonaio/sdk'), middle = mark('middle'), { Daytona: E, Image: I } = require('@daytonaio/sdk'), after = mark('after');";
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('app.js', input);
  const events: string[] = [];
  // When
  const output = applyEdits(input, imports(source, loadMapping(), '/').edits);
  runInNewContext(output, {
    mark: (name: string) => events.push(name),
    require: (name: string) => { events.push(name); return { Daytona: class {}, Sandbox: class {}, Image: class {} }; },
  });
  // Then
  expect(events).toEqual(['before', '@daytona/sdk', '@daytona/sdk/v0', 'middle', '@daytona/sdk', '@daytona/sdk/v0', 'after']);
});

it.each([
  "if (enabled) var { Daytona, Sandbox } = require('@daytonaio/sdk');",
  "for (var { Daytona, Sandbox } = require('@daytonaio/sdk'); enabled;) {}",
])('requests review rather than inserting statements into an unsafe context: %s', input => {
  // Given
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('app.js', input);
  // When
  const result = imports(source, loadMapping(), '/');
  // Then
  expect(applyEdits(input, result.edits)).toBe(input.replace('@daytonaio/sdk', '@daytona/sdk'));
  expect(result.items).toMatchObject([{ kind: 'private-import' }]);
});

it('preserves exported bindings when splitting a TypeScript CommonJS declaration', () => {
  // Given
  const input = "export const { Daytona, Sandbox } = require('@daytonaio/sdk');";
  const project = new Project({ useInMemoryFileSystem: true });
  const source = project.createSourceFile('app.ts', input);
  // When
  const output = applyEdits(input, imports(source, loadMapping(), '/').edits);
  // Then
  expect(project.createSourceFile('migrated.ts', output).getVariableStatements().map(statement => statement.isExported())).toEqual([true, true]);
});
