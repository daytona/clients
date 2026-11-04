/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { ParameterDeclaration, SourceFile } from 'ts-morph';
import type { Invocation } from './callback-parameter-values.js';
import type { Mapping } from './model.js';
import { annotatedKind, daytonaModule, isConstructor } from './bindings.js';
import { initializer, unwrap } from './edits.js';
import { importedReceiver } from './imported-receivers.js';
import type { ImportedReceiver } from './imported-receivers.js';
import { isProjectLocalModule, resolveProjectModule } from './project-modules.js';
import { factoryOrigin } from './factory-provenance.js';
import { combined, provenance } from './provenance-origin.js';
import type { Origin, Provenance } from './provenance-origin.js';
import { bindingAssignments } from './binding-assignments.js';
import { bindingValues, containerInsertions, memberValues, parameterValues } from './receiver-value-flow.js';
import type { PayloadSelection } from './receiver-value-flow.js';
import { memberName } from './member-access.js';

export type { Provenance } from './provenance-origin.js';
type ImportedPath = ImportedReceiver & { readonly path?: readonly (string | undefined)[] };

export class ExportProvenance {
  linkedDaytona = false;
  explicitIdentity = false;
  incomplete = false;
  // Invocation stack scopes return-parameter provenance; setter writes without
  // an active invocation deliberately inspect all calls that can write a binding.
  private readonly calls: Invocation[] = [];

  constructor(readonly mapping: Mapping, readonly workspaceNames: readonly string[]) {}

  hint(reference: ImportedPath, seen: ReadonlySet<Node> = new Set(), callable = false): Provenance {
    return provenance(this.hintOrigin(reference, seen, callable));
  }

  hintOrigin(reference: ImportedPath, seen: ReadonlySet<Node> = new Set(), callable = false): Origin {
    this.explicitIdentity ||= /daytona/i.test([reference.module, reference.name, reference.property, ...reference.path ?? []].join(' '));
    const target = resolveProjectModule(reference.source, reference.module);
    if (!isProjectLocalModule(reference.source, reference.module, this.workspaceNames)) {
      // Internal/absolute specifiers are opaque paths, not bare npm packages.
      return !target && /^(?:#|@\/|\/)/.test(reference.module) ? 'unknown' : 'not-daytona';
    }
    if (!target) return 'unknown';
    if (seen.has(target) || [...seen].filter(Node.isSourceFile).length >= 8) return 'unknown';
    const context = new Set(seen).add(target);
    const path = [...(reference.property === undefined ? [] : [reference.property]), ...reference.path ?? []];
    const name = reference.name === '*' ? path.shift() : reference.name;
    if (name === undefined) return callable ? 'unknown' : 'not-daytona';
    const exports = this.exports(target, name, context);
    if (exports.length === 0) return 'unknown';
    return combined(exports.map(value => {
      if (Node.isExportDeclaration(value) && value.getNamespaceExport()) {
        const module = value.getModuleSpecifierValue();
        return module ? this.hintOrigin({ source: value.getSourceFile(), module, name: '*', path }, context, callable) : 'unknown';
      }
      const evaluate = (node: Node): Origin => callable ? factoryOrigin(node, context, this) ?? 'unknown' : this.client(node, context);
      let member = value;
      for (const segment of path) {
        const object = unwrap(initializer(member) ?? member);
        if (segment === undefined || !Node.isObjectLiteralExpression(object)) return 'unknown';
        const property = object.getProperty(segment);
        if (!property) return 'unknown';
        member = property;
      }
      return evaluate(member);
    }));
  }

  private exports(source: SourceFile, name: string, seen: ReadonlySet<Node>): readonly Node[] {
    this.explicitIdentity ||= /daytona/i.test(name);
    const values: Node[] = [];
    if (name === 'default') values.push(...source.getExportAssignments().map(assignment => assignment.getExpression()));
    values.push(...source.getFunctions().filter(declaration => declaration.isExported() &&
      (name === 'default' ? declaration.isDefaultExport() : declaration.getName() === name)));
    for (const statement of source.getVariableStatements().filter(statement => statement.isExported())) {
      values.push(...statement.getDeclarations().filter(declaration => declaration.getName() === name));
    }
    for (const declaration of source.getExportDeclarations()) {
      const entry = declaration.getNamedExports().find(entry => (entry.getAliasNode()?.getText() ?? entry.getName()) === name);
      const star = name !== 'default' && !declaration.getNamedExports().length && !declaration.getNamespaceExport();
      const namespace = declaration.getNamespaceExport()?.getName() === name;
      if (!entry && !star && !namespace) continue;
      if (namespace) { values.push(declaration); continue; }
      const module = declaration.getModuleSpecifierValue();
      this.explicitIdentity ||= /daytona/i.test([module, entry?.getName()].join(' '));
      if (!module && entry) {
        values.push(...entry.getLocalTargetDeclarations().map(target => Node.isImportSpecifier(target) ?
          target.getAliasNode() ?? target.getNameNode() ?? target : target));
        continue;
      }
      if (!module) continue;
      if (!isProjectLocalModule(source, module, this.workspaceNames)) { values.push(declaration); continue; }
      const target = resolveProjectModule(source, module);
      if (!target) values.push(declaration);
      else {
        if (!seen.has(target) && [...seen].filter(Node.isSourceFile).length < 8) {
          values.push(...this.exports(target, entry?.getName() ?? name, new Set(seen).add(target)));
        } else values.push(declaration);
      }
    }
    // CommonJS exports are assignments rather than ESM export declarations.
    for (const statement of source.getStatements()) {
      if (!Node.isExpressionStatement(statement)) continue;
      const assignment = statement.getExpression();
      if (!Node.isBinaryExpression(assignment) || assignment.getOperatorToken().getKind() !== SyntaxKind.EqualsToken) continue;
      const left = assignment.getLeft().getText();
      if (left === `exports.${name}` || left === `module.exports.${name}` || name === 'default' && left === 'module.exports') {
        values.push(assignment.getRight());
      } else if (left === 'module.exports') {
        const object = assignment.getRight();
        if (Node.isObjectLiteralExpression(object)) {
          const property = object.getProperty(name);
          if (property) values.push(property);
        }
      }
    }
    return values;
  }

  parameterOrigin(parameter: ParameterDeclaration, seen: ReadonlySet<Node>, callable = false): Origin {
    // Reassignments are review evidence, not a new proof of the parameter's value.
    for (const write of bindingAssignments(parameter)) {
      if (Node.isBinaryExpression(write) && [SyntaxKind.EqualsToken, SyntaxKind.AmpersandAmpersandEqualsToken, SyntaxKind.BarBarEqualsToken,
        SyntaxKind.QuestionQuestionEqualsToken].includes(write.getOperatorToken().getKind())) this.client(write.getRight(), seen);
    }
    const supplied = parameterValues(parameter, this.calls);
    if (supplied === 'incomplete') { this.incomplete = true; return 'incomplete'; }
    const init = parameter.getInitializer();
    const values = supplied.length ? supplied : init ? [{ node: init, calls: this.calls }] : [];
    const origins = values.map(value => {
      const depth = this.calls.length;
      this.calls.push(...value.calls.slice(depth));
      try { return callable ? factoryOrigin(value.node, seen, this) ?? 'unknown' : this.client(value.node, seen); }
      finally { this.calls.length = depth; }
    });
    const origin = bindingAssignments(parameter).length ? 'unknown' : origins.length ? combined(origins) : 'unknown';
    return callable && origin === 'daytona' ? 'unknown' : origin;
  }

  private payloadOrigin(selection: PayloadSelection, seen: ReadonlySet<Node>): Origin {
    const evaluate = (member: Node): Origin => Node.isClassDeclaration(member) || Node.isClassExpression(member) || Node.isGetAccessorDeclaration(member) ?
      factoryOrigin(member, seen, this) ?? 'unknown' : Node.isSetAccessorDeclaration(member) ? 'not-daytona' : this.client(member, seen);
    if (selection.definite.length && selection.definite.every(member => evaluate(member) === 'not-daytona')) return 'not-daytona';
    for (const member of selection.evidence) evaluate(member);
    // Literal selection is review evidence, never an independent rewrite proof.
    return 'unknown';
  }

  client(node: Node, seen: ReadonlySet<Node> = new Set()): Origin {
    const value = unwrap(node);
    if (seen.has(value) || seen.size >= 64) return 'unknown';
    const context = new Set(seen).add(value);
    if (Node.isIdentifier(value)) this.explicitIdentity ||= /daytona/i.test(value.getText());
    if (isConstructor(value, this.mapping) || annotatedKind(value, this.mapping) === 'client') {
      this.linkedDaytona = true;
      return 'daytona';
    }
    if (Node.isVariableDeclaration(value) || Node.isParameterDeclaration(value) || Node.isPropertyDeclaration(value)) {
      const annotation = value.getTypeNode();
      const typeImport = Node.isTypeReference(annotation) ? importedReceiver(annotation.getTypeName()) : undefined;
      if (typeImport && !daytonaModule(typeImport.module, this.mapping) &&
        this.hintOrigin(typeImport, context) === 'not-daytona') return 'not-daytona';
    }
    if (Node.isBinaryExpression(value) && value.getOperatorToken().getKind() === SyntaxKind.CommaToken) {
      return this.client(value.getRight(), context);
    }
    if (Node.isConditionalExpression(value)) {
      return combined([this.client(value.getWhenTrue(), context), this.client(value.getWhenFalse(), context)]);
    }
    if (Node.isBinaryExpression(value) && [SyntaxKind.BarBarToken, SyntaxKind.QuestionQuestionToken,
      SyntaxKind.BarBarEqualsToken, SyntaxKind.QuestionQuestionEqualsToken].includes(value.getOperatorToken().getKind())) {
      const left = this.client(value.getLeft(), context);
      const right = this.client(value.getRight(), context);
      return left === 'undefined' ? right : combined([left, right]);
    }
    const imported = importedReceiver(value);
    if (imported) {
      if (!daytonaModule(imported.module, this.mapping)) return this.hintOrigin(imported, context);
      this.linkedDaytona = true;
      return 'unknown';
    }
    if (Node.isExportDeclaration(value)) {
      const module = value.getModuleSpecifierValue();
      if (!module) return 'unknown';
      if (daytonaModule(module, this.mapping)) { this.linkedDaytona = true; return 'unknown'; }
      return this.hintOrigin({ source: value.getSourceFile(), module, name: '*' }, context);
    }
    if (Node.isVariableDeclaration(value)) {
      const assignments = bindingAssignments(value);
      const init = initializer(value);
      const origins = [...(init ? [this.client(init, context)] : []), ...assignments.map(assignment =>
        Node.isBinaryExpression(assignment) && Node.isIdentifier(unwrap(assignment.getLeft())) &&
        [SyntaxKind.EqualsToken, SyntaxKind.BarBarEqualsToken, SyntaxKind.QuestionQuestionEqualsToken].includes(assignment.getOperatorToken().getKind()) ?
          this.client(assignment.getRight(), context) : 'unknown' as const)];
      return origins.length ? combined(origins) : value.getFirstAncestorByKind(SyntaxKind.ModuleDeclaration) ||
        value.getVariableStatement()?.isAmbient() ? 'unknown' : 'undefined';
    }
    if (Node.isParameterDeclaration(value)) return this.parameterOrigin(value, context);
    if (Node.isBindingElement(value)) {
      const selected = bindingValues(value, this.calls);
      return selected.length ? combined(selected.map(selection => this.payloadOrigin(selection, context))) : 'unknown';
    }
    const init = initializer(value);
    if (init) return this.client(init, context);
    if (Node.isShorthandPropertyAssignment(value)) return this.client(value.getNameNode(), context);
    if (Node.isIdentifier(value)) {
      if (value.getText() === 'undefined' && !value.getSymbol()?.getDeclarations().length) return 'undefined';
      const declarations = value.getSymbol()?.getDeclarations() ?? [];
      return declarations.length ? combined(declarations.map(declaration => this.client(declaration, context))) : 'unknown';
    }
    if (Node.isCallExpression(value) || Node.isNewExpression(value)) {
      const payloads = containerInsertions(value);
      if (payloads.length) {
        // Insertions link Daytona evidence, but neither cover initial contents
        // nor establish which write reaches this read.
        for (const payload of payloads) this.client(payload, context);
        return 'unknown';
      }
      const callee = unwrap(value.getExpression());
      // Native Promise.resolve forwards its payload through await; unrelated
      // methods (including a local Promise shim) do not establish value flow.
      if (Node.isCallExpression(value) && Node.isPropertyAccessExpression(callee) && callee.getName() === 'resolve') {
        const providers = callee.getSymbol()?.getDeclarations() ?? [];
        const program = value.getProject().getProgram().compilerObject;
        if (providers.length && providers.every(provider => program.isSourceFileDefaultLibrary(provider.getSourceFile().compilerNode))) {
          const payload = value.getArguments()[0];
          return payload ? this.client(payload, context) : 'undefined';
        }
      }
      // A native Map is a non-client; its opaque payload is a different value.
      if (Node.isCallExpression(value) && Node.isPropertyAccessExpression(callee) && callee.getName() === 'get' &&
        ['Map', 'ReadonlyMap', 'WeakMap'].includes(callee.getExpression().getType().getSymbol()?.getName() ?? '')) return 'unknown';
      if (Node.isCallExpression(value)) this.calls.push(value);
      let origin: Origin | undefined;
      try { origin = factoryOrigin(value.getExpression(), context, this); }
      finally { if (Node.isCallExpression(value)) this.calls.pop(); }
      if (origin !== undefined) return origin;
      if ((Node.isPropertyAccessExpression(callee) || Node.isElementAccessExpression(callee)) &&
        this.client(callee.getExpression(), context) === 'not-daytona') return 'not-daytona';
      // A concrete new instance can prove a local class / builtin, but an arbitrary
      // method result (container.get, resolver.resolve) is an injection boundary.
      const type = value.getType();
      return Node.isNewExpression(value) && !type.isAny() && !type.isUnknown() ? 'not-daytona' : 'unknown';
    }
    if (Node.isPropertyAccessExpression(value) || Node.isElementAccessExpression(value)) {
      if (Node.isPropertyAccessExpression(value)) this.explicitIdentity ||= /daytona/i.test(value.getName());
      let base: Node = value;
      const path: (string | undefined)[] = [];
      while (Node.isPropertyAccessExpression(base) || Node.isElementAccessExpression(base)) {
        path.unshift(memberName(base));
        base = unwrap(base.getExpression());
      }
      const imported = importedReceiver(base);
      if (imported && !daytonaModule(imported.module, this.mapping)) return this.hintOrigin({ ...imported, path }, context);
      const selected = memberValues(value, this.calls);
      if (selected.length) return combined(selected.map(selection => this.payloadOrigin(selection, context)));
      const providers = value.getSymbol()?.getDeclarations() ?? [];
      const origin = combined(providers.map(provider => this.client(provider, context)));
      if (providers.length && origin !== 'unknown') return origin;
      if (this.client(value.getExpression(), context) === 'not-daytona') return 'not-daytona';
    }
    if (Node.isObjectLiteralExpression(value) || Node.isArrayLiteralExpression(value) || Node.isLiteralExpression(value) ||
      Node.isFunctionDeclaration(value) || Node.isFunctionExpression(value) || Node.isArrowFunction(value) ||
      Node.isClassDeclaration(value) || Node.isClassExpression(value) || Node.isNullLiteral(value) ||
      value.getKind() === SyntaxKind.TrueKeyword || value.getKind() === SyntaxKind.FalseKeyword) return 'not-daytona';
    return 'unknown';
  }

}
