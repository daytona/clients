/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import { declarations } from './bindings.js';
import { unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';

export { isObjectAssign as isConfigAssign } from './intrinsics.js';

// Only inspect helpers reached from the constructor's config graph. Never
// infer a config from a field name elsewhere in a Daytona-importing file.
export function localConfigHelper(call: Node) {
  if (!Node.isCallExpression(call)) return undefined;
  const definitions = declarations(unwrap(call.getExpression()));
  if (definitions.length !== 1) return undefined;
  const definition = definitions[0];
  if (!definition || !bindingIsStable(definition)) return undefined;
  const callable = Node.isVariableDeclaration(definition) ? definition.getInitializer() : definition;
  if (callable?.getSourceFile() !== call.getSourceFile()) return undefined;
  if (Node.isFunctionDeclaration(callable) || Node.isArrowFunction(callable) || Node.isFunctionExpression(callable)) return callable;
  return undefined;
}
