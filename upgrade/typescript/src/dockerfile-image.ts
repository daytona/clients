/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import type { CallExpression } from 'ts-morph';
import type { Mapping } from './model.js';
import { declarations } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';
import { importedReceiver } from './imported-receivers.js';
import { isMemberAccess, memberName } from './member-access.js';

type Proof = NonNullable<Mapping['dockerfileImage']>['ts'];

/** A stable `const` binding's initializer, followed at most a few times. */
function settled(node: Node, depth = 0): Node {
  const value = unwrap(node);
  if (depth >= 4 || !Node.isIdentifier(value)) return value;
  const [declaration, ...others] = declarations(value);
  if (declaration === undefined || others.length > 0 || !Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return value;
  const init = initializer(declaration);
  return init === undefined ? value : settled(init, depth + 1);
}

/** The 0.x SDK modules a project imports the Image class from, before and after the default pass. */
function v0Module(mapping: Mapping, module: string): boolean {
  return mapping.v0Modules.ts.old.includes(module) || module === mapping.v0Modules.ts.v0;
}

/** `Image.fromDockerfile(...)`, `sdk.Image.fromDockerfile(...)`: the 0.x class, proven by its import. */
function isDockerfileImageCall(node: Node, proof: Proof, mapping: Mapping): boolean {
  const call = settled(node);
  if (!Node.isCallExpression(call)) return false;
  const callee = unwrap(call.getExpression());
  if (!isMemberAccess(callee) || memberName(callee) !== proof.method) return false;
  const owner = unwrap(callee.getExpression());
  const direct = importedReceiver(owner);
  if (direct !== undefined) return direct.property === undefined ? direct.name === proof.type && v0Module(mapping, direct.module)
    : direct.property === proof.type && (direct.name === '*' || direct.name === 'default') && v0Module(mapping, direct.module);
  if (!isMemberAccess(owner) || memberName(owner) !== proof.type) return false;
  const namespace = importedReceiver(owner.getExpression());
  return namespace !== undefined && namespace.property === undefined && (namespace.name === '*' || namespace.name === 'default') &&
    v0Module(mapping, namespace.module);
}

/**
 * Whether the call's parameters object passes, as its image parameter, the
 * result of the 0.x `Image.fromDockerfile(...)`: an inline object literal (a
 * variable could be written to before the call) of plain properties only,
 * with exactly one such property. A spread, a computed key, an accessor or a
 * method could replace it, so any of them defeats the proof.
 */
export function passesDockerfileImage(call: CallExpression, mapping: Mapping): boolean {
  const proof = mapping.dockerfileImage?.ts;
  const [first] = call.getArguments();
  if (proof === undefined || first === undefined) return false;
  const parameters = unwrap(first);
  if (!Node.isObjectLiteralExpression(parameters)) return false;
  const properties = parameters.getProperties();
  const plain = properties.every((property) => Node.isShorthandPropertyAssignment(property) ||
    Node.isPropertyAssignment(property) && !Node.isComputedPropertyName(property.getNameNode()));
  if (!plain) return false;
  const named = properties.filter((property) =>
    (Node.isPropertyAssignment(property) || Node.isShorthandPropertyAssignment(property)) && property.getName() === proof.parameter);
  if (named.length !== 1) return false;
  const [property] = named;
  if (Node.isPropertyAssignment(property)) {
    const value = property.getInitializer();
    return value !== undefined && isDockerfileImageCall(value, proof, mapping);
  }
  if (!Node.isShorthandPropertyAssignment(property)) return false;
  const [declaration, ...others] = property.getValueSymbol()?.getDeclarations() ?? [];
  if (declaration === undefined || others.length > 0 || !Node.isVariableDeclaration(declaration) || !bindingIsStable(declaration)) return false;
  const init = initializer(declaration);
  return init !== undefined && isDockerfileImageCall(init, proof, mapping);
}
