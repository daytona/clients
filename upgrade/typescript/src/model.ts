/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

export type Mode = 'upgrade' | 'suggest-v2' | 'remove-suggestions';
export type Category = 'direct' | 'differs' | 'none';
export type ConfigAction = 'keep' | 'deprecated' | 'url' | 'fixme';
export type Receiver = {
  readonly ts: { readonly types: readonly string[]; readonly members: readonly string[] };
  readonly parent?: string;
  readonly attr?: { readonly ts: string | null };
  readonly producedBy?: readonly string[];
};
export type Suggestion = {
  readonly id: string; readonly receiver: string;
  readonly v0: { readonly ts: string | null };
  readonly v2: { readonly ts: string | null };
  readonly category: Category;
  readonly notes: { readonly ts: readonly string[] };
  readonly anchor: string; readonly startsWorkflow: boolean;
};
export type Mapping = {
  readonly docs: { readonly base: string; readonly placeholder: boolean };
  /** The pinned 0.x release SDK 2.x bundles as `.v0`. */
  readonly v0Version: string;
  readonly packages: { readonly ts: { readonly old: readonly string[]; readonly new: string; readonly range: string } };
  readonly v0Modules: { readonly ts: { readonly old: readonly string[]; readonly root: string; readonly v0: string; readonly keep: readonly string[] } };
  readonly rootNames: { readonly ts: readonly string[] };
  /** 0.x root error aliases the 2.x root dropped, each mapped to the shared category it named. */
  readonly rootAliasCategories: Readonly<Record<string, string>>;
  readonly v0Exports?: { readonly ts: readonly string[] };
  readonly movedClientMembers: { readonly ts: readonly string[] };
  readonly config: { readonly ts: Readonly<Record<string, ConfigAction>> };
  readonly cloudApiUrls: readonly string[];
  readonly receivers: Readonly<Record<string, Receiver>>;
  readonly suggestions: readonly Suggestion[];
  readonly dataTraps: readonly { readonly id: string; readonly text: string; readonly short?: string }[];
};
export type SuggestionReport = {
  readonly id: string; readonly receiver: string; readonly v0: string;
  readonly v2: string | null; readonly category: Category;
  readonly notes: readonly string[]; readonly docs: string;
};
export type ItemKind = 'fixme' | 'config-field' | 'deprecated-target' | 'custom-api-url' |
  'env-api-url' | 'private-import' | 'dynamic-import' | 'mock-target' | 'peer-dependency' |
  'unsupported-file' | 'types-unresolved' | 'suggestion' | 'workflow' | 'other-ecosystem' | 'manual-dependency';
export type Item = {
  readonly file: string; readonly line: number; readonly column: number;
  readonly kind: ItemKind; readonly message: string; readonly suggestion?: SuggestionReport;
};
export type Dependency = { readonly file: string; readonly from: string; readonly to: string };
/** Advice that is not a manual item: it never fails `--strict`. */
export type Warning = { readonly file: string; readonly message: string };
export type Refusal = {
  readonly reason: 'dirty-tree' | 'custom-api-url' | 'no-daytona' | 'bad-arguments' | 'newer-v0';
  readonly message: string;
};
export type Report = {
  readonly tool: 'daytona-upgrade'; readonly version: string; readonly language: 'ts';
  readonly mode: Mode; readonly root: string; readonly dryRun: boolean;
  readonly changedFiles: readonly string[]; readonly dependencies: readonly Dependency[];
  readonly items: readonly Item[]; readonly nextSteps: readonly string[]; readonly refused?: Refusal;
  readonly warnings?: readonly Warning[];
};
export type Options = {
  readonly root: string; readonly mode: Mode; readonly dry: boolean; readonly print: boolean;
  readonly json: boolean; readonly force: boolean; readonly yes: boolean;
  readonly dropApiUrl: boolean; readonly strict: boolean;
  /** Migrate a project on a newer 0.x minor than the bundled `.v0` (default false). */
  readonly allowNewerV0?: boolean;
};
export type Edit = { readonly start: number; readonly end: number; readonly text: string };
export type Change = { readonly file: string; readonly before: string; readonly after: string };
export class UpgradeError extends Error {
  override readonly name = 'UpgradeError';
  constructor(readonly detail: string) { super(detail); }
}
export function assertNever(value: never): never { throw new UpgradeError(`Unexpected variant: ${String(value)}`); }
