/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import type { CallExpression } from 'ts-morph';
import { unwrap } from './edits.js';
import { propertyKey } from './binding-key.js';
import { mutationPath } from './stable-bindings.js';

export type ReflectedWrite = {
  readonly call: CallExpression;
  readonly target: Node;
  // Undefined means a computed key or opaque source can replace any member.
  readonly members: readonly string[] | undefined;
};

export function reflectedWrite(call: CallExpression): ReflectedWrite | undefined {
  const path = mutationPath(call);
  if (!path || !/^(?:Reflect\.(?:set|defineProperty)|Object\.(?:assign|defineProperty|defineProperties))$/u.test(path)) return undefined;
  const [first, key, , receiver] = call.getArguments();
  // Reflect.set uses its optional receiver for the property write, not the lookup target.
  const target = path === 'Reflect.set' ? receiver ?? first : first;
  if (!target) return undefined;
  if (path === 'Object.assign' || path === 'Object.defineProperties') {
    const members: string[] = [];
    for (const input of path === 'Object.assign' ? call.getArguments().slice(1) : key ? [key] : []) {
      const value = unwrap(input);
      if (!Node.isObjectLiteralExpression(value)) return { call, target, members: undefined };
      for (const property of value.getProperties()) {
        const name = propertyKey(property);
        if (name === undefined) return { call, target, members: undefined };
        members.push(name);
      }
    }
    return { call, target, members };
  }
  const value = key && unwrap(key);
  return { call, target, members: Node.isStringLiteral(value) || Node.isNoSubstitutionTemplateLiteral(value) ? [value.getLiteralValue()] : undefined };
}
