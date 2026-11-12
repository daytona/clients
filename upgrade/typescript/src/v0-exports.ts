/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Node } from 'ts-morph';
import type { Item, Mapping } from './model.js';
import { item } from './edits.js';

export function isSharedError(name: string, mapping: Mapping): boolean {
  return mapping.rootNames.ts.includes(name) && /^Daytona.*Error$/.test(name);
}

export function staysAtRoot(name: string, mapping: Mapping): boolean {
  return ['v0', 'v2'].includes(name) || mapping.rootNames.ts.includes(name) && !isSharedError(name, mapping);
}

export function missingV0Export(node: Node, name: string, context: { readonly mapping: Mapping; readonly root: string }): readonly Item[] {
  if (!context.mapping.v0Exports || context.mapping.v0Exports.ts.includes(name)) return [];
  return [item(node, { kind: 'private-import', message: `${name} is not exported by bundled v${context.mapping.v0Version}; use the v2 API or remove it. It is unavailable from @daytona/sdk/v0.` }, context.root)];
}
