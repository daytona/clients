/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { CallExpression, NewExpression, ParameterDeclaration, Project } from 'ts-morph';
import { unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { objectIdentity } from './object-identity.js';
import { reflectedWrite } from './reflected-writes.js';

export type MemberWrite = {
  readonly effect: Node;
  readonly target: Node;
  readonly members: readonly string[] | undefined;
};

const effectsByProject = new WeakMap<Project, readonly MemberWrite[]>();

type Invocation = CallExpression | NewExpression;

/** A spread obscures positions; retain every identity carried by its inputs. */
function spreadTargets(node: Node, seen: Set<Node> = new Set()): readonly Node[] {
  const value = objectIdentity(node);
  if (seen.has(value)) return [];
  seen.add(value);
  if (Node.isFunctionLikeDeclaration(value) || Node.isClassDeclaration(value) || Node.isClassExpression(value)) return [];
  return [value, ...value.getChildren().flatMap(child => spreadTargets(child, seen))];
}

function argumentTargets(call: Invocation, parameter: ParameterDeclaration, position: number): readonly Node[] {
  const args = call.getArguments();
  const argument = args[position];
  const fallback = parameter.getInitializer();
  if (args.slice(0, position + 1).some(Node.isSpreadElement)) {
    return [...args.flatMap(arg => spreadTargets(arg)), ...(fallback ? [fallback] : [])];
  }
  return fallback && (!argument || argument.getType().isUndefined() || Node.isVoidExpression(unwrap(argument)))
    ? [fallback] : argument ? [argument] : [];
}

function directWrite(node: Node): MemberWrite | undefined {
  if (!Node.isBinaryExpression(node)) return undefined;
  const operator = node.getOperatorToken().getKind();
  if (operator < SyntaxKind.FirstAssignment || operator > SyntaxKind.LastAssignment) return undefined;
  const access = unwrap(node.getLeft());
  if (!isMemberAccess(access)) return undefined;
  const name = memberName(access);
  return { effect: node, target: access.getExpression(), members: name === undefined ? undefined : [name] };
}

/** Carry parameter mutations to their actual arguments, including nested helper calls. */
export function memberWriteEffects(node: Node): readonly MemberWrite[] {
  const project = node.getProject();
  const cached = effectsByProject.get(project);
  if (cached) return cached;
  const callers = new Map<Node, Invocation[]>();
  const writes: MemberWrite[] = [];
  for (const source of project.getSourceFiles().filter(source => !source.isDeclarationFile())) {
    for (const effect of source.getDescendants()) {
      if (Node.isCallExpression(effect) || Node.isNewExpression(effect)) {
        const callee = objectIdentity(effect.getExpression());
        const owners = Node.isNewExpression(effect) && (Node.isClassDeclaration(callee) || Node.isClassExpression(callee))
          ? callee.getConstructors() : [callee];
        for (const owner of owners) {
          const identity = objectIdentity(owner);
          const previous = callers.get(identity);
          if (previous) previous.push(effect);
          else callers.set(identity, [effect]);
        }
        const write = Node.isCallExpression(effect) ? reflectedWrite(effect) : undefined;
        if (write) writes.push({ effect, target: write.target, members: write.members });
      }
      const write = directWrite(effect);
      if (write) writes.push(write);
    }
  }
  // Effects form a finite call-site/target/member graph, even for recursive helpers.
  const seen = new Map<Node, Map<Node, Set<string>>>();
  const effects: MemberWrite[] = [];
  for (let index = 0; index < writes.length; index++) {
    const write = writes[index];
    if (!write) continue;
    const target = objectIdentity(write.target);
    const targets = seen.get(write.effect) ?? new Map<Node, Set<string>>();
    seen.set(write.effect, targets);
    const members = targets.get(target) ?? new Set<string>();
    targets.set(target, members);
    const key = JSON.stringify(write.members) ?? '*';
    if (members.has(key)) continue;
    members.add(key);
    effects.push(write);
    if (!Node.isParameterDeclaration(target)) continue;
    const owner = target.getParent();
    if (!Node.isFunctionLikeDeclaration(owner)) continue;
    const position = owner.getParameters().indexOf(target);
    for (const call of callers.get(objectIdentity(owner)) ?? []) {
      for (const argument of argumentTargets(call, target, position)) {
        writes.push({ effect: call, target: argument, members: write.members });
      }
    }
  }
  effectsByProject.set(project, effects);
  return effects;
}
