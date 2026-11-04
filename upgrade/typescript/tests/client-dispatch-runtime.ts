/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { runInNewContext } from 'node:vm';
import { ts } from 'ts-morph';

// Deliberately distinct root/v0 results make bypassed overrides observable without a network.
class Daytona {
  readonly v0 = { create: () => 99, get: () => 23 };
  create(): number { return 7; }
  get(): number { return 11; }
}

export function execute(source: string): unknown {
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  return runInNewContext(compiled.outputText, { exports: {}, require: () => ({ Daytona }) });
}
