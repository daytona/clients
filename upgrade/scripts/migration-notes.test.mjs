/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const mapping = JSON.parse(readFileSync(new URL('../mapping/mapping.json', import.meta.url), 'utf8'));
const guide = readFileSync(new URL('../MIGRATION.md', import.meta.url), 'utf8');

for (const language of ['py', 'ts']) {
  for (const id of ['sandbox.downloadUrl', 'sandbox.uploadUrl']) {
    test(`${language} ${id} guidance distinguishes permanent and bounded links`, () => {
      // Given: a v0 file-URL call whose TTL may be outside v2's range.
      const entry = mapping.suggestions.find((candidate) => candidate.id === id);
      assert.ok(entry);
      // When: the customer reads the mapped guidance and its generated guide row.
      const notes = entry.notes[language].join(' ');
      const row = guide.split('\n').find((line) => line.startsWith(`| \`sandbox.${entry.v0[language]}(`));
      assert.ok(row);
      // Then: neither permanent links nor the seven-day ceiling is lost.
      for (const text of [notes, row]) {
        assert.match(text, /<=\s*0/);
        assert.match(text, /never.expir/i);
        assert.match(text, /1\s+to\s+604800/);
        assert.match(text, /7.days/);
      }
    });
  }

  test(`${language} commit guidance identifies the result-field migration`, () => {
    // Given: a consumer of the v0 commit result, not only its arguments.
    const entry = mapping.suggestions.find((candidate) => candidate.id === 'sandbox.git.commit');
    assert.ok(entry);
    // When: reading the migration notes and the generated guide.
    const notes = entry.notes[language].join(' ');
    const rows = guide.split('\n').filter((line) => line.startsWith('| `sandbox.git.commit('));
    // Then: the old result accessor cannot be mistaken for the v2 accessor.
    for (const text of [notes, ...rows]) {
      assert.match(text, /\.sha\b.*\.hash\b/);
    }
    assert.equal(rows.length, 2);
  });
}

test('direct suggestions carry no notes, because direct comments never print them', () => {
  // Given: every mapped suggestion.
  // When: selecting the rows rendered without their notes.
  const noted = mapping.suggestions.filter((entry) => entry.category === 'direct' && (entry.notes.py.length || entry.notes.ts.length));
  // Then: a caveat requires the differs category, so the comment shows it.
  assert.deepEqual(noted.map((entry) => entry.id), []);
});

test('the root close() is never suggested as a v2-namespace close', () => {
  // Given: 2.x closes both namespace clients through the root close().
  // When / Then: no row maps close onto {client}.v2.close().
  assert.equal(mapping.suggestions.some((entry) => entry.receiver === 'client' && (entry.v0.py === 'close' || entry.v0.ts === 'close')), false);
});
