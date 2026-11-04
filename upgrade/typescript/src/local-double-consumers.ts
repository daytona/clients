/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { ParameterDeclaration, SourceFile } from 'ts-morph';
import { consumerRequirements, movingMember } from './double-consumer-flow.js';
import type { ConsumerRequirement } from './double-consumer-flow.js';
import { receiverConstraints } from './receiver-constraints.js';
import type { Receivers } from './receivers.js';
import { ReceiverPackages } from './receiver-packages.js';

export type DoubleConsumers = {
  readonly receivers: Receivers;
  readonly sources: ReadonlySet<SourceFile>;
  readonly reviews: Set<Node>;
};

export function rewrittenParameter(parameter: ParameterDeclaration, context: DoubleConsumers): readonly ConsumerRequirement[] {
  const source = parameter.getSourceFile();
  if (!context.sources.has(source) || source.isDeclarationFile() || source.isInNodeModules()) return [];
  const owner = parameter.getParent();
  if (Node.isFunctionDeclaration(owner) && !owner.getBody()) {
    const actual = owner.getImplementation()?.getParameters()[owner.getParameters().indexOf(parameter)];
    return actual ? rewrittenParameter(actual, context) : [];
  }
  if (Node.isFunctionTypeNode(owner)) {
    const variable = owner.getParent();
    const implementation = Node.isVariableDeclaration(variable) ? variable.getInitializer() : undefined;
    if (!Node.isArrowFunction(implementation) && !Node.isFunctionExpression(implementation)) return [];
    const actual = implementation.getParameters()[owner.getParameters().indexOf(parameter)];
    return actual ? rewrittenParameter(actual, context) : [];
  }
  return consumerRequirements(parameter.getNameNode(), context);
}

/** A signature is not enough: the parameter's implementation must move too. */
export function migratedParameter(parameter: ParameterDeclaration, context: DoubleConsumers): boolean {
  const { receivers, sources } = context;
  const source = parameter.getSourceFile();
  if (!sources.has(source) || source.isDeclarationFile() || source.isInNodeModules() || parameter.isRestParameter()) return false;
  const owner = parameter.getParent();
  if (Node.isFunctionDeclaration(owner) && !owner.getBody()) {
    const implementation = owner.getImplementation();
    const actual = implementation?.getParameters()[owner.getParameters().indexOf(parameter)];
    return actual !== undefined && migratedParameter(actual, context);
  }
  const packages = new ReceiverPackages();
  const roots = receiverConstraints(parameter.getType());
  if (!roots.length || !roots.every(type => {
    const declarations = type.getSymbol()?.getDeclarations() ?? [];
    return declarations.length > 0 && declarations.every(declaration => Node.isClassDeclaration(declaration) &&
      declaration.getName() === 'Daytona' && packages.packageRoot(declaration.getSourceFile().getFilePath()) !== undefined);
  }) || !receivers.client(parameter)) return false;
  if (Node.isFunctionTypeNode(owner)) {
    const variable = owner.getParent();
    const implementation = Node.isVariableDeclaration(variable) ? variable.getInitializer() : undefined;
    if (!Node.isArrowFunction(implementation) && !Node.isFunctionExpression(implementation)) return false;
    const actual = implementation.getParameters()[owner.getParameters().indexOf(parameter)];
    return actual !== undefined && migratedParameter(actual, context);
  }
  // Method dispatch can select a project override instead of this body.
  if (!Node.isFunctionDeclaration(owner) && !Node.isFunctionExpression(owner) && !Node.isArrowFunction(owner)) return false;
  const body = owner.getBody();
  if (!body || body.getDescendantsOfKind(SyntaxKind.Identifier).some(identifier =>
    ['arguments', 'eval', 'Function'].includes(identifier.getText()))) return false;
  const name = parameter.getNameNode();
  if (!Node.isIdentifier(name)) return false;
  const uses = name.findReferencesAsNodes();
  return uses.length > 0 && uses.every(use => movingMember(use, receivers) !== undefined);
}
