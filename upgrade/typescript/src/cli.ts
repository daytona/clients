#!/usr/bin/env node
/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { pathToFileURL } from 'node:url';
import { realpathSync } from 'node:fs';
import { argumentsFor, help } from './arguments.js';
import { emptyReport, run, version } from './run.js';
import { loadMapping } from './mapping.js';
import type { Mapping } from './model.js';
import { diff, humanReport } from './output.js';
import { relativePath } from './discovery.js';

type IO = { readonly out: (text: string) => void; readonly err: (text: string) => void };
const standardIO: IO = { out: text => { process.stdout.write(text); }, err: text => { process.stderr.write(text); } };
export async function main(argv: readonly string[], io: IO = standardIO, mapping?: Mapping): Promise<number> {
  let parsed: ReturnType<typeof argumentsFor>;
  try { parsed = argumentsFor(argv); }
  catch (error) {
    if (!(error instanceof Error)) throw error;
    const fallback = argumentsFor([]).options;
    const report = { ...emptyReport(fallback), refused: { reason: 'bad-arguments' as const, message: error.message } };
    if (argv.includes('--json')) io.out(JSON.stringify(report) + '\n');
    else io.err(`Refused: ${error.message}\n${help}`);
    return 2;
  }
  if (parsed.help) { io.out(help); return 0; }
  if (parsed.version) { io.out(version() + '\n'); return 0; }
  try {
    const result = await run(parsed.options, mapping ?? loadMapping());
    if (parsed.options.json) io.out(JSON.stringify(result.report, null, 2) + '\n');
    else {
      if (parsed.options.dry) io.out(diff(result.changes, parsed.options.root));
      else if (parsed.options.print) for (const change of result.changes) {
        io.out(`--- ${relativePath(parsed.options.root, change.file)}\n${change.after}${change.after.endsWith('\n') ? '' : '\n'}`);
      }
      io.out(humanReport(result.report));
    }
    return result.code;
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    const message = error.message.replaceAll(parsed.options.root + '/', '').replaceAll(parsed.options.root + '\\', '');
    if (parsed.options.json) io.out(JSON.stringify({ ...emptyReport(parsed.options), nextSteps: [`Error: ${message}`] }) + '\n');
    io.err(`daytona-upgrade: ${message}\n`);
    return 1;
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  process.stdout.on('error', (error: NodeJS.ErrnoException) => {
    if (error.code === 'EPIPE') process.exit(0);
    throw error;
  });
  process.exitCode = await main(process.argv.slice(2));
}
