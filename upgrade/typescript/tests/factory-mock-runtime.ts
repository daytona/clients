/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';

type Mock = {
  (): unknown;
  mockReturnValue(value: unknown): Mock;
  mockImplementation(value: () => unknown): Mock;
};

function fn(implementation?: () => unknown): Mock {
  let current = implementation;
  const mock: Mock = Object.assign(() => current?.(), {
    mockReturnValue(value: unknown): Mock { current = () => value; return mock; },
    mockImplementation(value: () => unknown): Mock { current = value; return mock; },
  });
  return mock;
}

/** Execute real rewritten modules with only the test framework's replacement seam faked. */
export function executeFactory(files: Readonly<Record<string, string>>): unknown {
  const modules = new Map<string, Record<string, unknown>>();
  const framework = {
    fn,
    mocked: (value: unknown): unknown => value,
    mock(name: string, factory?: () => Record<string, unknown>): void {
      const exports = factory ? factory() : Object.fromEntries(Object.keys(load(name)).map(key => [key, fn()]));
      Object.assign(load(name), exports);
    },
    spyOn(target: object, key: string): Mock {
      const mock = fn();
      Object.assign(target, { [key]: mock });
      return mock;
    },
  };
  const load = (name: string): Record<string, unknown> => {
    const cached = modules.get(name);
    if (cached) return cached;
    if (name === '@daytonaio/sdk' || name === '@daytona/sdk') return { Daytona: class {
      create(): number { return 7; }
      readonly v0 = { create: () => 99 };
    } };
    const source = files[`${name.replace(/^\.\//u, '')}.ts`];
    if (source === undefined) throw new TypeError(`Missing fixture module: ${name}`);
    const exports: Record<string, unknown> = {};
    modules.set(name, exports);
    const compiled = ts.transpileModule(source, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    } });
    runInNewContext(compiled.outputText, { exports, require: load, jest: framework, vi: framework });
    return exports;
  };
  return load('./index')['result'];
}
