/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import type { Options } from './model.js';
import { UpgradeError } from './model.js';

export const help = `daytona-upgrade [path] [options]

  (default)             Upgrade 0.x code to SDK 2.x on the v0 namespace and bump the dependency
  --suggest-v2          Write daytona-v2: suggestion comments; no code or dependency change
  --remove-suggestions  Remove daytona-v2: suggestion comments
  --dry                 Show the diff, write nothing (skips the Git check)
  --print               Print changed files to stdout instead of writing them (skips the Git check)
  --json                Print the report as JSON (with --suggest-v2, writes nothing)
  --force               Run on a Git tree with uncommitted changes
  -y, --yes             Do not prompt (automatic when stdin is not a TTY)
  --drop-api-url        Accept API URLs the tool cannot verify or that come from the environment, asserting they point at Daytona Cloud
  --strict              Exit 1 when manual items remain
  --allow-newer-v0      Migrate a project on a newer 0.x minor than the 0.x release SDK 2.x runs as .v0
  -v, --version / -h, --help
`;
export function argumentsFor(argv: readonly string[]): { readonly options: Options; readonly help: boolean; readonly version: boolean } {
  const { values, positionals } = parseArgs({ args: [...argv], allowPositionals: true, options: {
    'suggest-v2': { type: 'boolean' }, 'remove-suggestions': { type: 'boolean' },
    dry: { type: 'boolean' }, print: { type: 'boolean' }, json: { type: 'boolean' }, force: { type: 'boolean' },
    yes: { type: 'boolean', short: 'y' },
    'drop-api-url': { type: 'boolean' }, strict: { type: 'boolean' }, 'allow-newer-v0': { type: 'boolean' },
    version: { type: 'boolean', short: 'v' }, help: { type: 'boolean', short: 'h' },
  } });
  if (positionals.length > 1) throw new UpgradeError('Expected at most one project path.');
  if (values['suggest-v2'] && values['remove-suggestions']) throw new UpgradeError('--suggest-v2 and --remove-suggestions are mutually exclusive.');
  return { options: { root: resolve(positionals[0] ?? '.'), mode: values['suggest-v2'] ? 'suggest-v2' : values['remove-suggestions'] ? 'remove-suggestions' : 'upgrade',
    dry: values.dry ?? false, print: values.print ?? false, json: values.json ?? false, force: values.force ?? false,
    yes: values.yes ?? false, dropApiUrl: values['drop-api-url'] ?? false, strict: values.strict ?? false,
    allowNewerV0: values['allow-newer-v0'] ?? false },
    help: values.help ?? false, version: values.version ?? false };
}
