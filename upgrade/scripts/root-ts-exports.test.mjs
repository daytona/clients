/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { collectRootTsExports } from './root-ts-exports.mjs';
import './v0-ts-exports.test.mjs';
import './v0-exports-schema.test.mjs';

const cases = JSON.parse(readFileSync(new URL('./root-ts-exports.cases.json', import.meta.url), 'utf8'));
for (const fixture of cases) {
  test(`root exports: ${fixture.name}`, () => {
    // Given: an SDK root using the fixture's export syntax.
    const source = fixture.text;
    // When: the compiler-backed scanner collects the public names.
    const result = collectRootTsExports(source);
    // Then: supported names are complete, or unsupported syntax fails closed.
    if (fixture.unsupported) assert.ok(result.problems.length > 0);
    else {
      assert.deepEqual(result.problems, []);
      assert.deepEqual([...result.names].sort(), [...fixture.names].sort());
    }
  });
}
