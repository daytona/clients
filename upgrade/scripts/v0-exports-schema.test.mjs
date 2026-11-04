/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

for (const language of ['py', 'ts']) {
  for (const invalid of ['empty', 'duplicate']) {
    test(`rejects ${invalid} pinned ${language} exports before scanning sources`, (t) => {
      // Given: a mapping with an invalid public import surface.
      const root = mkdtempSync(join(tmpdir(), 'daytona-v0-schema-'));
      t.after(() => rmSync(root, { recursive: true, force: true }));
      const scripts = join(root, 'scripts');
      const mappingDir = join(root, 'mapping');
      mkdirSync(scripts);
      mkdirSync(mappingDir);
      for (const name of ['root-ts-exports.mjs', 'v0-ts-exports.mjs']) {
        symlinkSync(fileURLToPath(new URL(name, import.meta.url)), join(scripts, name));
      }
      copyFileSync(new URL('check-v0-surface.mjs', import.meta.url), join(scripts, 'check-v0-surface.mjs'));
      copyFileSync(new URL('../mapping/mapping.schema.json', import.meta.url), join(mappingDir, 'mapping.schema.json'));
      const mapping = JSON.parse(readFileSync(new URL('../mapping/mapping.json', import.meta.url), 'utf8'));
      mapping.v0Exports[language] = invalid === 'empty' ? [''] : ['Daytona', 'Daytona'];
      writeFileSync(join(mappingDir, 'mapping.json'), JSON.stringify(mapping));
      // When: the real surface gate validates the mapping.
      const result = spawnSync(process.execPath, [join(scripts, 'check-v0-surface.mjs'), '--src', join(root, 'unused')], { encoding: 'utf8' });
      // Then: it rejects the offending schema path, not a missing source tree.
      assert.equal(result.status, 1);
      assert.match(result.stderr, new RegExp(`\\$\\.v0Exports\\.${language}(?:\\[0\\])?: ${invalid === 'empty' ? 'empty string' : 'duplicate items'}`));
    });
  }
}
