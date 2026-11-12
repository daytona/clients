/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readFileSync } from 'node:fs';
import type { Category, ConfigAction, Mapping, Receiver, Suggestion } from './model.js';
import { UpgradeError } from './model.js';
import { version, withTarget } from './sdk-target.js';

// The bundled asset is trusted; parsing still earns its type without an assertion.
function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new UpgradeError('Invalid mapping object');
  return Object.fromEntries(Object.entries(value));
}
function string(value: unknown): string {
  if (typeof value !== 'string') throw new UpgradeError('Invalid mapping string');
  return value;
}
function strings(value: unknown): readonly string[] {
  if (!Array.isArray(value)) throw new UpgradeError('Invalid mapping array');
  return value.map((entry: unknown) => string(entry));
}
function array(value: unknown): readonly unknown[] {
  if (!Array.isArray(value)) throw new UpgradeError('Invalid mapping array');
  return value;
}
function ts(value: unknown): Record<string, unknown> { return object(object(value)['ts']); }
function tsName(value: unknown): string | null {
  const name = object(value)['ts'];
  return name === null ? null : string(name);
}
function category(value: unknown): Category {
  if (value === 'direct' || value === 'differs' || value === 'none') return value;
  throw new UpgradeError('Invalid suggestion category');
}
function action(value: unknown): ConfigAction {
  if (value === 'keep' || value === 'deprecated' || value === 'url' || value === 'fixme') return value;
  throw new UpgradeError('Invalid config action');
}
export function parseMapping(value: unknown): Mapping {
  const data = object(value);
  const packages = ts(data['packages']);
  const modules = ts(data['v0Modules']);
  const docs = object(data['docs']);
  const exports = data['v0Exports'] === undefined ? undefined : object(data['v0Exports'])['ts'];
  const receivers: Record<string, Receiver> = {};
  for (const [key, raw] of Object.entries(object(data['receivers']))) {
    const entry = object(raw);
    const language = ts(entry);
    receivers[key] = {
      ts: { types: strings(language['types']), members: strings(language['members']) },
      ...(entry['parent'] === undefined ? {} : { parent: string(entry['parent']) }),
      ...(entry['attr'] === undefined ? {} : { attr: { ts: tsName(entry['attr']) } }),
      ...(entry['producedBy'] === undefined ? {} : { producedBy: strings(entry['producedBy']) }),
    };
  }
  const suggestions: Suggestion[] = array(data['suggestions']).map(raw => {
    const entry = object(raw);
    return { id: string(entry['id']), receiver: string(entry['receiver']),
      v0: { ts: tsName(entry['v0']) }, v2: { ts: tsName(entry['v2']) },
      // A language without a v2 form has no equivalent there.
      category: tsName(entry['v2']) === null ? 'none' : category(entry['category']), notes: { ts: strings(object(entry['notes'])['ts']) },
      anchor: string(entry['anchor']), startsWorkflow: entry['startsWorkflow'] === true,
      ...(entry['dockerfileImage'] === undefined ? {} : { dockerfileImage: {
        v2: { ts: tsName(object(entry['dockerfileImage'])['v2']) },
        notes: { ts: strings(object(object(entry['dockerfileImage'])['notes'])['ts']) } } }) };
  });
  const dockerfileImage = data['dockerfileImage'] === undefined ? undefined : ts(data['dockerfileImage']);
  return {
    docs: { base: string(docs['base']), placeholder: docs['placeholder'] === true },
    v0Version: string(data['v0Version']),
    packages: { ts: { old: strings(packages['old']), new: string(packages['new']), range: string(packages['range']) } },
    v0Modules: { ts: { old: strings(modules['old']), root: string(modules['root']), v0: string(modules['v0']), keep: strings(modules['keep']) } },
    rootNames: { ts: strings(object(data['rootNames'])['ts']) },
    rootAliasCategories: data['rootAliasCategories'] === undefined ? {}
      : Object.fromEntries(Object.entries(object(data['rootAliasCategories'])).map(([alias, category]) => [alias, string(category)])),
    ...(exports === undefined ? {} : { v0Exports: { ts: strings(exports) } }),
    movedClientMembers: { ts: strings(object(data['movedClientMembers'])['ts']) },
    config: { ts: Object.fromEntries(Object.entries(ts(data['config'])).map(([name, value]) => [name, action(value)])) },
    cloudApiUrls: strings(data['cloudApiUrls']), receivers, suggestions,
    dataTraps: array(data['dataTraps']).map(raw => {
      const trap = object(raw);
      return { id: string(trap['id']), text: string(trap['text']),
        ...(trap['short'] === undefined ? {} : { short: string(trap['short']) }) };
    }),
    ...(dockerfileImage === undefined ? {} : { dockerfileImage: { ts: {
      type: string(dockerfileImage['type']), method: string(dockerfileImage['method']), parameter: string(dockerfileImage['parameter']) } } }),
  };
}
export function loadMapping(): Mapping {
  const value: unknown = JSON.parse(readFileSync(new URL('./mapping.json', import.meta.url), 'utf8'));
  return withTarget(parseMapping(value), version());
}
