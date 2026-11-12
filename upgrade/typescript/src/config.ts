/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind } from 'ts-morph';
import type { SourceFile } from 'ts-morph';
import { cleanRemoval, item, unwrap } from './edits.js';
import { findConfigs } from './config-sources.js';
import { declarations, inheritsClientConstructor } from './bindings.js';
import type { Edit, Item, Mapping } from './model.js';
import { assertNever } from './model.js';
import { isCloudApiUrl } from './cloud-api-url.js';
import { constructorValues, propertyInput } from './constructor-inputs.js';
import { propertyKey } from './binding-key.js';
import { configReaders } from './config-readers.js';
import type { ConfigReaders } from './config-readers.js';

export { findConfigs } from './config-sources.js';
export const UNRESOLVED_CONFIG = 'cannot verify the API URL of this DaytonaConfig; if it targets Daytona Cloud, rerun with --drop-api-url';
export const ASSERTED_CONFIG = '--drop-api-url asserts Cloud usage; review this unchanged call manually.';
export function configs(source: SourceFile, mapping: Mapping, context: {
  readonly root: string; readonly dropApiUrl: boolean; readonly configObjects?: readonly Node[];
}): { readonly edits: readonly Edit[]; readonly items: readonly Item[]; readonly reviews: readonly { readonly node: Node; readonly message: string }[] } {
  const objects = context.configObjects ?? findConfigs(source, mapping).filter(object => object.getSourceFile() === source);
  const edits: Edit[] = [];
  const items: Item[] = [];
  const readers = new Map<Node, ConfigReaders>();
  const reviews = new Map<Node, string>();
  const removable = (property: Node): boolean => {
    const owner = Node.isBinaryExpression(property) ? property : property.getParentOrThrow();
    const usage = readers.get(owner) ?? configReaders(owner, mapping);
    readers.set(owner, usage);
    if (!usage.shared) return true;
    const message = 'Keep this shared config unchanged for its other readers; SDK 2.x refuses URL fields, so pass this constructor a config without apiUrl or serverUrl.';
    if (usage.constructors.length) for (const constructor of usage.constructors) reviews.set(constructor, message);
    else items.push(item(property, { kind: 'config-field', message }, context.root));
    return false;
  };
  const unresolved = (node: Node): void => {
    items.push(item(node, context.dropApiUrl ? { kind: 'config-field', message: ASSERTED_CONFIG }
      : { kind: 'custom-api-url', message: UNRESOLVED_CONFIG }, context.root));
  };
  const field = (property: Node, name: string, value: Node | undefined): boolean => {
    const action = Object.hasOwn(mapping.config.ts, name) ? mapping.config.ts[name] : undefined;
    if (action === undefined) return false;
    switch (action) {
      case 'keep': return false;
      case 'deprecated':
        items.push(item(property, { kind: 'deprecated-target', message: 'target is deprecated in SDK 2.x; pass the target on each v0 call instead.' }, context.root));
        return false;
      case 'fixme':
        items.push(item(property, { kind: 'config-field', message: `Remove DaytonaConfig.${name}: SDK 2.x does not accept this 0.x field.` }, context.root));
        return false;
      case 'url': {
        const expression = value && unwrap(value);
        const inputs = expression ? constructorValues(expression) : [];
        const literal = expression && (Node.isStringLiteral(expression) || Node.isNoSubstitutionTemplateLiteral(expression)) ? expression.getLiteralValue() : undefined;
        const cloud = inputs.length > 0 && inputs.every(input =>
          (Node.isStringLiteral(input) || Node.isNoSubstitutionTemplateLiteral(input)) && isCloudApiUrl(input.getLiteralValue(), mapping));
        const defaultUrl = Node.isNullLiteral(expression) || Node.isIdentifier(expression) &&
          expression.getText() === 'undefined' && declarations(expression).length === 0;
        const customInput = inputs.some(input =>
          (Node.isStringLiteral(input) || Node.isNoSubstitutionTemplateLiteral(input)) && !isCloudApiUrl(input.getLiteralValue(), mapping));
        if (customInput) {
          items.push(item(property, { kind: 'custom-api-url', message: 'SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.' }, context.root));
          return false;
        }
        const owner = property.getParent();
        if (Node.isObjectLiteralExpression(owner) && propertyInput(owner, name).length === 0) {
          unresolved(property);
          return false;
        }
        if (Node.isBinaryExpression(property)) {
          const operator = property.getOperatorToken().getKind();
          const assignsValue = operator === SyntaxKind.EqualsToken || operator === SyntaxKind.BarBarEqualsToken ||
            operator === SyntaxKind.QuestionQuestionEqualsToken || operator === SyntaxKind.AmpersandAmpersandEqualsToken;
          if (!assignsValue || literal === undefined && !defaultUrl || !Node.isExpressionStatement(property.getParent())) {
            unresolved(property);
            return false;
          }
        }
        if (cloud || defaultUrl) return removable(property);
        if (context.dropApiUrl) {
          if (!removable(property)) return false;
          items.push(item(property, { kind: 'config-field', message: `Dropped non-literal ${name}: --drop-api-url asserts it points at Daytona Cloud.` }, context.root));
          return true;
        }
        unresolved(property);
        return false;
      }
      default: return assertNever(action);
    }
  };
  for (const object of new Map(objects.map(node => [node.getStart(), node])).values()) {
    if (Node.isBinaryExpression(object)) {
      const access = object.getLeft();
      const key = Node.isElementAccessExpression(access) ? access.getArgumentExpression() : undefined;
      const name = Node.isPropertyAccessExpression(access) ? access.getName() : Node.isStringLiteral(key) ? key.getLiteralValue() : undefined;
      if (name !== undefined) {
        if (field(object, name, object.getRight())) {
          const owner = object.getParentIfKindOrThrow(SyntaxKind.ExpressionStatement);
          const parent = owner.getParent();
          const standalone = Node.isSourceFile(parent) || Node.isBlock(parent) || Node.isCaseClause(parent) || Node.isDefaultClause(parent);
          const edit = { start: owner.getStart(), end: owner.getEnd(), text: standalone ? '' : ';' };
          edits.push(standalone ? cleanRemoval(owner, edit) : edit);
        }
      } else unresolved(object);
      continue;
    }
    if (!Node.isObjectLiteralExpression(object)) { unresolved(object); continue; }
    const removed = new Set<Node>();
    const properties = object.getProperties();
    for (const property of properties) {
      const name = propertyKey(property);
      if (name === undefined) continue;
      if (!(Node.isPropertyAssignment(property) || Node.isShorthandPropertyAssignment(property))) {
        if (mapping.config.ts[name] === 'url') unresolved(property);
        continue;
      }
      const value = Node.isPropertyAssignment(property) ? property.getInitializer() : property.getNameNode();
      if (field(property, name, value)) removed.add(property);
    }
    const owner = object.getParent();
    if (removed.size > 0 && removed.size === properties.length && Node.isNewExpression(owner) &&
      inheritsClientConstructor(owner.getExpression(), mapping) &&
      owner.getArguments().length === 1 && owner.getArguments()[0] === object) {
      edits.push({ start: object.getStart(), end: object.getEnd(), text: '' });
      continue;
    }
    for (const [index, property] of properties.entries()) {
      if (!removed.has(property)) continue;
      const next = property.getNextSiblingIfKind(SyntaxKind.CommaToken);
      const previous = property.getPreviousSiblingIfKind(SyntaxKind.CommaToken);
      const previousProperty = properties[index - 1];
      const leading = !next && previous && previousProperty && !removed.has(previousProperty) ? previous : undefined;
      const multiline = leading && /[\r\n]/.test(source.getFullText().slice(leading.getEnd(), property.getStart()));
      if (leading && multiline) edits.push({ start: leading.getStart(), end: leading.getEnd(), text: '' });
      // `a,  c` would be left by removing `b,` from `a, b, c`: take the
      // spaces after the comma too when the line goes on.
      const spaces = next ? /^[\t ]*(?=[^\s])/.exec(source.getFullText().slice(next.getEnd()))?.[0].length ?? 0 : 0;
      edits.push(cleanRemoval(property, { start: !multiline && leading ? leading.getStart() : property.getStart(),
        end: next ? next.getEnd() + spaces : property.getEnd(), text: '' }));
    }
  }
  return { edits, items, reviews: [...reviews].map(([node, message]) => ({ node, message })) };
}
