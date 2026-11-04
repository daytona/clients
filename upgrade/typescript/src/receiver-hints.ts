/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node } from 'ts-morph';
import type { Mapping } from './model.js';
import { assertNever } from './model.js';
import { unwrap } from './edits.js';
import { ExportProvenance } from './export-provenance.js';
import { projectDependsOnDaytona, projectImportsDaytona } from './project-daytona.js';
import { unresolvedType } from './external-receiver-kind.js';
import { isNamespace } from './bindings.js';
import { ambiguousObjectMember } from './object-member-proof.js';

function explicitIdentity(node: Node): boolean {
  if (Node.isIdentifier(node)) return /daytona/i.test(node.getText());
  if (Node.isCallExpression(node) || Node.isNewExpression(node)) return explicitIdentity(node.getExpression());
  if (Node.isPropertyAccessExpression(node)) return /daytona/i.test(node.getName()) || explicitIdentity(node.getExpression());
  if (Node.isElementAccessExpression(node)) return explicitIdentity(node.getExpression());
  return false;
}

export function hasReceiverHint(node: Node, mapping: Mapping, workspaceNames: readonly string[]): boolean {
  const value = unwrap(node, true);
  // An SDK module namespace is not a client instance.
  if (isNamespace(value, mapping)) return false;
  // Proof-grade SDK types were handled by Receivers; other concrete types
  // are conclusive negatives, including structurally compatible registries.
  if (!unresolvedType(value.getType()) && !ambiguousObjectMember(value)) return false;
  const trace = new ExportProvenance(mapping, workspaceNames);
  const origin = trace.client(value);
  if (trace.incomplete) return projectImportsDaytona(node.getSourceFile());
  switch (origin) {
    case 'incomplete': return projectImportsDaytona(node.getSourceFile());
    case 'not-daytona': return false;
    case 'daytona': return true;
    case 'unknown': case 'undefined':
      return trace.linkedDaytona || (trace.explicitIdentity || explicitIdentity(value)) && projectDependsOnDaytona(node.getSourceFile());
    default: return assertNever(origin);
  }
}
