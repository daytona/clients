/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Node, SyntaxKind, ts } from 'ts-morph';
import type { SourceFile, CallExpression } from 'ts-morph';
import type { Edit, Item, Mapping, Receiver, Suggestion, SuggestionReport } from './model.js';
import { assertNever } from './model.js';
import { applyEdits, commentEdit, displayText, location, removeSuggestionLines, statement, unwrap } from './edits.js';
import { Receivers } from './receivers.js';
import { declarations } from './bindings.js';
import { initializer } from './edits.js';
import { bindingIsStable } from './stable-bindings.js';
import { isMemberAccess, memberName } from './member-access.js';
import { sandboxOwner } from './sandbox-owner.js';
import { suggestionCommentEdits } from './comment-tokens.js';
import { errorImportSuggestions } from './error-import-suggestions.js';
import { renderOptionalArguments, withNamedArguments } from './optional-arguments.js';

function referencesBinding(node: Node, binding: Node, seen: Set<Node> = new Set()): boolean {
  const value = unwrap(node);
  if (seen.has(value)) return false;
  seen.add(value);
  if (value.getSymbol() === binding.getSymbol()) return true;
  if (Node.isPropertyAccessExpression(value)) return referencesBinding(value.getExpression(), binding, seen);
  return declarations(value).some(declaration => {
    if (!bindingIsStable(declaration)) return false;
    const init = initializer(declaration);
    return init !== undefined && referencesBinding(init, binding, seen);
  });
}

export function suggest(source: SourceFile, mapping: Mapping, root: string): { readonly after: string; readonly items: readonly Item[] } {
  const receivers = new Receivers(mapping);
  const edits: Edit[] = [...suggestionCommentEdits(source)];
  const items: Item[] = [];
  const blocks = new Map<Node, string[]>();
  for (const [node, message] of errorImportSuggestions(source, mapping)) {
    items.push({ ...location(node, root), kind: 'suggestion', message });
    const owner = statement(node);
    blocks.set(owner, [...(blocks.get(owner) ?? []), `daytona-v2: ${message}`]);
  }
  const blockIds = new Map<Node, Set<string>>();
  const headers = new Map<Node, string[]>();
  const traps = mapping.dataTraps.filter(trap => trap.id !== 'same-platform').map(trap => displayText(trap.short ?? trap.text));
  const dataDocs = `${mapping.docs.base}#data`;
  const found: { readonly call: CallExpression; readonly suggestion: SuggestionReport; readonly startsWorkflow: boolean }[] = [];
  const unprovenClient = (call: CallExpression, access: Node): void => {
    items.push({ ...location(call, root), kind: 'fixme', message: `Cannot prove the owning root Daytona client for ${displayText(access.getText())}; review the v2 equivalent manually.` });
  };
  const clientRoot = (node: Node, use: Node, seen: Set<Node> = new Set()): Node | undefined => {
    const value = unwrap(node);
    if (seen.has(value)) return undefined;
    seen.add(value);
    if (receivers.client(value)) {
      if ([value, ...value.getDescendants()].some(part => part.getKind() === SyntaxKind.ThisKeyword) &&
        value.getFirstAncestor(part => ts.isFunctionLike(part.compilerNode)) !== use.getFirstAncestor(part => ts.isFunctionLike(part.compilerNode))) return undefined;
      const identifiers = [value, ...value.getDescendants()].filter(Node.isIdentifier);
      const visible = use.getSymbolsInScope(ts.SymbolFlags.Value | ts.SymbolFlags.Alias)
        .map(symbol => (symbol.getAliasedSymbol() ?? symbol).getExportSymbol());
      const accessible = identifiers.every(identifier => {
        if (Node.isPropertyAccessExpression(identifier.getParent()) && identifier.getParent().getLastChild() === identifier) return true;
        const symbol = identifier.getSymbol();
        return symbol === undefined || visible.includes((symbol.getAliasedSymbol() ?? symbol).getExportSymbol());
      });
      return accessible ? node : undefined;
    }
    if (isMemberAccess(value)) {
      const name = memberName(value);
      if (name === 'v0' || Object.values(mapping.receivers).some(kind => kind.parent === 'client' && kind.attr?.ts === name)) {
        return clientRoot(value.getExpression(), use, seen);
      }
    }
    for (const declaration of declarations(value)) {
      if (!bindingIsStable(declaration) || declaration.getSourceFile() !== use.getSourceFile()) continue;
      const init = initializer(declaration);
      if (init) {
        const owner = clientRoot(init, use, seen);
        if (owner) return owner;
      }
    }
    return undefined;
  };
  // The default pass moves `client.<member>` to `client.v0.<member>` on this
  // proof alone; a call directly on that member (or on the client), before or
  // after that move, is the mapping row the migrated code gets, so suggest it
  // the same way.
  // Code that already holds the 0.x client (`client.v0`) and hands it out
  // (an argument, an alias) or writes one of its members may replace its
  // services.
  const escapedV0 = new Map<string, boolean>();
  const v0Escapes = (v0: Node): boolean => {
    const text = v0.getText();
    const cached = escapedV0.get(text);
    if (cached !== undefined) return cached;
    const escapes = source.getDescendants().some(node => {
      if (!isMemberAccess(node) || node.getText() !== text) return false;
      const parent = node.getParent();
      if (parent === undefined || !isMemberAccess(parent) || parent.getExpression() !== node) return true;
      const write = parent.getParent();
      return Node.isBinaryExpression(write) && write.getLeft() === parent &&
        write.getOperatorToken().getKind() >= SyntaxKind.FirstAssignment && write.getOperatorToken().getKind() <= SyntaxKind.LastAssignment;
    });
    escapedV0.set(text, escapes);
    return escapes;
  };
  const rootProvenEntry = (access: Node): Suggestion | undefined => {
    let candidate: Node = access;
    while (isMemberAccess(candidate)) {
      const member = memberName(candidate);
      const owner = candidate.getExpression();
      if (member !== undefined && mapping.movedClientMembers.ts.includes(member) &&
          (isMemberAccess(owner) && memberName(owner) === 'v0' ? receivers.client(owner.getExpression()) && !v0Escapes(owner) : receivers.client(owner, member))) {
        const method = memberName(access);
        if (candidate === access) return mapping.suggestions.find(row => row.receiver === 'client' && row.v0.ts === method);
        if (isMemberAccess(access) && access.getExpression() === candidate) {
          const service = Object.entries(mapping.receivers).find(([, kind]) => kind.parent === 'client' && kind.attr?.ts === member)?.[0];
          return mapping.suggestions.find(row => row.receiver === service && row.v0.ts === method);
        }
        return undefined;
      }
      candidate = candidate.getExpression();
    }
    return undefined;
  };
  for (const call of source.getDescendantsOfKind(SyntaxKind.CallExpression)) {
    const access = call.getExpression();
    if (!isMemberAccess(access)) continue;
    const entry = receivers.suggestion(call) ?? rootProvenEntry(access);
    if (!entry) {
      // Use the default pass's proof seam, not a receiver-name heuristic: a
      // proven root access can still have an unprovable service below it.
      let candidate: Node = access;
      while (isMemberAccess(candidate)) {
        const member = memberName(candidate);
        if (member !== undefined && mapping.movedClientMembers.ts.includes(member) &&
            receivers.client(candidate.getExpression(), member)) {
          unprovenClient(call, access);
          break;
        }
        candidate = candidate.getExpression();
      }
      continue;
    }
    const receiver = displayText(access.getExpression().getText());
    const ownerClient = entry.receiver.startsWith('client') ? clientRoot(access.getExpression(), call) : undefined;
    if (entry.receiver.startsWith('client') && !ownerClient) {
      unprovenClient(call, access);
      continue;
    }
    const client = ownerClient ? displayText(ownerClient.getText()) : 'daytona';
    // A handle a sandbox call produced (an LSP server, a PTY) is not its sandbox.
    const sandboxProduced = mapping.receivers[entry.receiver]?.producedBy?.some(id => id.startsWith('sandbox')) === true;
    const ownerSandbox = entry.receiver.startsWith('sandbox') || sandboxProduced ? sandboxOwner(access.getExpression(), call, receivers) : undefined;
    const sandbox = ownerSandbox ? displayText(ownerSandbox.getText()) : receiver;
    // A typed service can be proven without its sandbox. Render its own path,
    // not a guessed sandbox that would turn `files` into `files.fs`.
    const servicePath: string[] = [];
    let kind: string | undefined = entry.receiver;
    while (kind && kind !== 'sandbox' && kind.startsWith('sandbox.')) {
      const service: Receiver | undefined = mapping.receivers[kind];
      if (service?.attr?.ts) servicePath.unshift(service.attr.ts);
      kind = service?.parent;
    }
    if (!ownerSandbox && entry.v2.ts?.includes('{sandbox}') && (sandboxProduced || servicePath.length > 0 &&
      entry.v2.ts.replaceAll(`{sandbox}.${servicePath.join('.')}.`, `${receiver}.`).includes('{sandbox}'))) {
      const form = displayText(withNamedArguments(entry.v2.ts).replaceAll('{sandbox}', 'sandbox').replaceAll('{client}', client));
      const message = `move this call to the owning sandbox: ${form}`;
      items.push({ ...location(call, root), kind: 'fixme', message });
      const owner = statement(call);
      const line = `FIXME(daytona-upgrade): ${message}`;
      const start = owner.getFullStart();
      const end = owner.getStart();
      const previous = applyEdits(source.getFullText().slice(start, end), edits
        .filter(edit => edit.start >= start && edit.end <= end)
        .map(edit => ({ ...edit, start: edit.start - start, end: edit.end - start })));
      if (!previous.includes(commentEdit(owner, [line]).text.trim())) blocks.set(owner, [...new Set([...(blocks.get(owner) ?? []), line])]);
      continue;
    }
    const callArguments = call.getArguments();
    const passed = (name: string, index: number): string | undefined => {
      // A spread that could hold it renders the argument by its name.
      if (callArguments.slice(0, index + 1).some((argument) => Node.isSpreadElement(argument))) return name;
      const argument = callArguments[index];
      if (argument === undefined || (Node.isIdentifier(argument) && argument.getText() === 'undefined')) return undefined;
      return displayText(argument.getText());
    };
    const substitute = (text: string): string => {
      const filled = renderOptionalArguments(text, passed);
      const serviceForm = !ownerSandbox && servicePath.length ? filled.replaceAll(`{sandbox}.${servicePath.join('.')}`, receiver) : filled;
      return displayText(serviceForm.replaceAll('{client}', client).replaceAll('{sandbox}', sandbox));
    };
    const suggestion: SuggestionReport = { id: entry.id, receiver: entry.receiver, v0: displayText(access.getText()),
      v2: entry.v2.ts === null ? null : substitute(entry.v2.ts), category: entry.category,
      notes: entry.notes.ts.map(substitute), docs: `${mapping.docs.base}#${entry.anchor}` };
    found.push({ call, suggestion, startsWorkflow: entry.startsWorkflow });
    const owner = statement(call);
    const ids = blockIds.get(owner) ?? new Set<string>();
    if (ids.has(entry.id)) continue;
    ids.add(entry.id);
    blockIds.set(owner, ids);
    const message = suggestion.v2 ?? 'No v2 equivalent';
    items.push({ ...location(call, root), kind: 'suggestion', message, suggestion });
    const lines = [`daytona-v2: ${message}`];
    switch (suggestion.category) {
      case 'direct': break;
      case 'differs': for (const note of suggestion.notes) lines.push(`daytona-v2:   differs: ${note}`); break;
      case 'none': for (const note of suggestion.notes) lines.push(`daytona-v2:   none: ${note}`); break;
      default: assertNever(suggestion.category);
    }
    lines.push(`daytona-v2:   docs: ${suggestion.docs}`);
    blocks.set(owner, [...(blocks.get(owner) ?? []), ...lines]);
  }
  for (const producer of found.filter(entry => entry.startsWorkflow)) {
    const variable = producer.call.getFirstAncestorByKind(SyntaxKind.VariableDeclaration);
    if (!variable || !bindingIsStable(variable) || unwrap(variable.getInitializerOrThrow()) !== producer.call) continue;
    const binding = variable.getNameNode();
    if (!Node.isIdentifier(binding)) continue;
    const scope = producer.call.getFirstAncestor(part => ts.isFunctionLike(part.compilerNode)) ?? source;
    const isBinding = (node: Node | undefined): boolean => Node.isIdentifier(node) && node.getSymbol() === binding.getSymbol();
    const methods: string[] = [];
    const lifecycleMethods: string[] = [];
    for (const entry of found) {
      const sameScope = (entry.call.getFirstAncestor(part => ts.isFunctionLike(part.compilerNode)) ?? source) === scope;
      if (entry.call === producer.call || sameScope && entry.call.getStart() < producer.call.getStart() ||
        scope !== source && !entry.call.getAncestors().includes(scope)) continue;
      const access = entry.call.getExpression();
      if (!isMemberAccess(access)) continue;
      const kind = entry.suggestion.receiver;
      const method = memberName(access);
      const needsLifecycleReview = kind === 'sandbox' && ['start', 'stop', 'pause', 'archive', 'setAutostopInterval',
        'setAutoPauseInterval', 'setAutoArchiveInterval'].includes(method ?? '');
      if ((kind === 'sandbox' || kind.startsWith('sandbox.')) && referencesBinding(access.getExpression(), binding)) {
        const child = kind === 'sandbox' ? '' : `${kind.slice('sandbox.'.length)}.`;
        methods.push(`${child}${memberName(access)}`);
        if (needsLifecycleReview) lifecycleMethods.push(`${method}`);
      } else if (kind === 'client' || kind.startsWith('client.')) {
        const passesSandbox = entry.call.getArguments().some(argument => isBinding(argument) ||
          Node.isObjectLiteralExpression(argument) && argument.getProperties().some(property =>
            Node.isShorthandPropertyAssignment(property) ? property.getValueSymbol() === binding.getSymbol() :
              Node.isPropertyAssignment(property) && isBinding(property.getInitializer())));
        if (passesSandbox) {
          methods.push(displayText(access.getText()));
          if (kind === 'client' && (method === 'start' || method === 'stop')) lifecycleMethods.push(displayText(access.getText()));
        }
      }
    }
    const owner = statement(producer.call);
    const message = lifecycleMethods.length ?
      `workflow \`${binding.getText()}\`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: ${[...new Set(lifecycleMethods)].join(', ')}` : methods.length ?
      `workflow \`${binding.getText()}\`: move these v0 calls on it to v2 together: ${[...new Set(methods)].join(', ')}` :
      `workflow \`${binding.getText()}\`: move this sandbox to v2 as a unit`;
    headers.set(owner, [...(headers.get(owner) ?? []), `daytona-v2: ${message}`]);
    items.push({ ...location(producer.call, root), kind: 'workflow', message,
      suggestion: { ...producer.suggestion, id: `workflow:${binding.getText()}`, notes: traps, docs: dataDocs } });
  }
  for (const [owner, lines] of blocks) {
    const workflow = headers.get(owner);
    edits.push(commentEdit(owner, workflow ? [...workflow, `daytona-v2:   data: ${traps.join('; ')}`, `daytona-v2:   docs: ${dataDocs}`, ...lines] : lines));
  }
  return { after: applyEdits(source.getFullText(), edits), items };
}
export { removeSuggestionLines };
