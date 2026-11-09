/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, ts } from 'ts-morph';
import type { Block, CatchClause, NewExpression, SourceFile } from 'ts-morph';
import type { Mapping } from './model.js';
import { importedName, isClientConstructor } from './bindings.js';
import { displayText, unwrap } from './edits.js';
import { isMemberAccess, memberName } from './member-access.js';
import { isSharedError } from './v0-exports.js';

function handlesDaytonaError(handler: CatchClause, mapping: Mapping): boolean {
  // Inspect only the handler's own execution, never deferred function bodies.
  let checksType = false;
  let checksDaytona = false;
  const block = handler.getBlock();
  block.forEachDescendant((node, traversal) => {
    if (ts.isFunctionLike(node.compilerNode)) { traversal.skip(); return; }
    if (Node.isTypeOfExpression(node)) checksType = true;
    if (!Node.isBinaryExpression(node) || node.getOperatorToken().getKind() !== SyntaxKind.InstanceOfKeyword) return;
    checksType = true;
    const name = importedName(unwrap(node.getRight()), mapping);
    // The former root aliases (rootAliasCategories) name shared categories too.
    if (name !== undefined && (isSharedError(name, mapping) || Object.hasOwn(mapping.rootAliasCategories, name))) checksDaytona = true;
    const type = unwrap(node.getRight());
    if (Node.isIdentifier(type) && type.getText() === 'Error' &&
      !type.getSymbol()?.getDeclarations()?.some(declaration => declaration.getSourceFile() === type.getSourceFile())) {
      checksDaytona = true;
    }
  });
  return checksDaytona || !checksType && !block.getStatements().some(Node.isThrowStatement);
}

function receiverBinding(construction: NewExpression): Node | undefined {
  const owner = construction.getAncestors().find(parent => unwrap(parent) !== construction);
  const target = Node.isVariableDeclaration(owner) ? owner.getNameNode() :
    Node.isBinaryExpression(owner) && owner.getOperatorToken().getKind() === SyntaxKind.EqualsToken &&
      unwrap(owner.getRight()) === construction ? owner.getLeft() : undefined;
  return target && (Node.isIdentifier(target) || isMemberAccess(target)) ? target : undefined;
}

function sameReceiver(left: Node, right: Node): boolean {
  const a = unwrap(left); const b = unwrap(right);
  if (a === b) return true;
  if (Node.isIdentifier(a) && Node.isIdentifier(b)) return a.getSymbol() !== undefined && a.getSymbol() === b.getSymbol();
  if (isMemberAccess(a) && isMemberAccess(b)) {
    return memberName(a) === memberName(b) && sameReceiver(a.getExpression(), b.getExpression());
  }
  return a.getKind() === SyntaxKind.ThisKeyword && b.getKind() === SyntaxKind.ThisKeyword;
}

function evaluatesV0(construction: NewExpression, block: Block, receivers: readonly Node[]): boolean {
  const binding = receiverBinding(construction);
  return receivers.some(receiver => {
    const matches = unwrap(receiver) === construction || binding !== undefined &&
      receiver.getStart() > construction.getEnd() && sameReceiver(receiver, binding);
    if (!matches) return false;
    const boundary = receiver.getAncestors().find(ancestor => ancestor === block || ts.isFunctionLike(ancestor.compilerNode));
    if (boundary !== block) return false;
    // Prove evaluation through eager expressions only. Branch joins, nested blocks
    // unrelated to the construction, callbacks and optional chains stay manual.
    let child = receiver;
    for (const ancestor of receiver.getAncestors()) {
      if (Node.isBlock(ancestor)) {
        const scopes = construction.getAncestors().filter(Node.isBlock);
        const shared = scopes.indexOf(ancestor);
        if (shared < 0) return false;
        // Only plain blocks may unwind into a later access in an enclosing block.
        if (scopes.slice(0, shared).some(scope => !Node.isBlock(scope.getParent()))) return false;
        // An earlier control-flow statement may return before reaching this access.
        return !scopes.slice(0, shared + 1).flatMap(scope => scope.getStatements()).some(statement => statement.getStart() > construction.getEnd() &&
          statement.getEnd() <= receiver.getStart() && !Node.isExpressionStatement(statement) &&
          !Node.isVariableStatement(statement) && !Node.isEmptyStatement(statement));
      }
      if (ts.isOptionalChain(ancestor.compilerNode)) return false;
      // An if condition always evaluates, unlike its branches.
      if (Node.isIfStatement(ancestor) && ancestor.getExpression() === child) { child = ancestor; continue; }
      if (Node.isBinaryExpression(ancestor)) {
        const operator = ancestor.getOperatorToken().getKind();
        if (ancestor.getRight() === child && [SyntaxKind.AmpersandAmpersandToken, SyntaxKind.BarBarToken,
          SyntaxKind.QuestionQuestionToken, SyntaxKind.AmpersandAmpersandEqualsToken,
          SyntaxKind.BarBarEqualsToken, SyntaxKind.QuestionQuestionEqualsToken].includes(operator)) return false;
      } else if (!(Node.isPropertyAccessExpression(ancestor) || Node.isElementAccessExpression(ancestor) ||
        Node.isCallExpression(ancestor) || Node.isNewExpression(ancestor) || Node.isAwaitExpression(ancestor) ||
        Node.isParenthesizedExpression(ancestor) || Node.isAsExpression(ancestor) || Node.isTypeAssertion(ancestor) ||
        Node.isNonNullExpression(ancestor) || Node.isExpressionStatement(ancestor) || Node.isReturnStatement(ancestor) ||
        Node.isThrowStatement(ancestor) || Node.isVariableDeclaration(ancestor) ||
        Node.isVariableDeclarationList(ancestor) || Node.isVariableStatement(ancestor))) return false;
      child = ancestor;
    }
    return false;
  });
}

/** Constructor validation moved to lazy .v0 creation, outside old startup catches. */
export function reportConstructorErrors(source: SourceFile, mapping: Mapping, context: {
  readonly manual: (node: Node, message: string) => void;
  readonly v0Receivers: readonly Node[];
}): void {
  for (const construction of source.getDescendantsOfKind(SyntaxKind.NewExpression)) {
    if (!isClientConstructor(construction.getExpression(), mapping)) continue;
    let child: Node = construction;
    for (const ancestor of construction.getAncestors()) {
      if (ts.isFunctionLike(ancestor.compilerNode)) break;
      const handler = Node.isTryStatement(ancestor) && ancestor.getTryBlock() === child ? ancestor.getCatchClause() : undefined;
      if (handler && handlesDaytonaError(handler, mapping)) {
        if (Node.isBlock(child) && evaluatesV0(construction, child, context.v0Receivers)) break;
        const binding = receiverBinding(construction);
        const receiver = binding ? displayText(binding.getText()) : undefined;
        const advice = receiver ? `access ${receiver}.v0 inside this try (for example, const v0 = ${receiver}.v0)` :
          'access .v0 on this client inside the try';
        context.manual(construction, `SDK 2.x builds the v0 client on first use, so credential errors no longer happen in this constructor; ${advice} to keep the error handling.`);
        break;
      }
      child = ancestor;
    }
  }
}
