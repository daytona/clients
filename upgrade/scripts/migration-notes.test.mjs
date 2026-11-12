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

  test(`${language} code-run guidance points the v0 chart artifacts at the v2 result`, () => {
    // Given: a consumer of the v0 code-run artifacts, not only its arguments.
    const entry = mapping.suggestions.find((candidate) => candidate.id === 'sandbox.process.codeRun');
    assert.ok(entry);
    // When: reading the migration notes and the generated guide.
    const notes = entry.notes[language].join(' ');
    const rows = guide.split('\n').filter((line) => /^\| `sandbox\.process\.(code_run|codeRun)\(/.test(line));
    // Then: both name where the charts and their images are in v2.
    for (const text of [notes, ...rows]) {
      assert.match(text, /artifacts\.charts becomes result\.charts\b.*result\.results\[i\]\.png/);
    }
    assert.equal(rows.length, 2);
  });

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

// SDK 2.x reads a wait budget above the timer maximum (2,147,483.647 s, about
// 24.8 days) as no deadline, so a 0.x fork that waited without a deadline maps
// to a budget above it rather than to a finite one.
const TIMER_MAXIMUM_SECONDS = 2_147_483.647;
// Every fork row and language that must carry the suggestion. The client rows
// are TypeScript-only: the 0.x Python client had no fork (its v0.py is null),
// so they have no Python form.
const FORK_ROWS = [
  ['sandbox.fork', ['py', 'ts']],
  ['sandbox._experimental_fork', ['py', 'ts']],
  ['client.fork', ['ts']],
  ['client._experimental_fork', ['ts']],
];
for (const [id, languages] of FORK_ROWS) {
  for (const language of languages) {
    test(`${language} ${id} keeps a 0.x unbounded wait unbounded`, () => {
      // Given: the fork suggestion and its notes, which must exist.
      const entry = mapping.suggestions.find((candidate) => candidate.id === id);
      assert.ok(entry, `missing mapping row ${id}`);
      const suggestion = entry.v2[language];
      assert.equal(typeof suggestion, 'string', `missing ${language} suggestion for ${id}`);
      const notes = entry.notes[language].join(' ');
      // When: reading the budget it passes for a 0.x timeout of 0.
      const budget = /\b(\d+) \* 86_400\b/.exec(suggestion);
      // Then: it is above the timer maximum, and the notes give that reason.
      assert.ok(budget, suggestion);
      assert.ok(Number(budget[1]) * 86_400 > TIMER_MAXIMUM_SECONDS);
      assert.match(notes, /above about 24\.8 days \(the timer maximum\) as no deadline/);
      assert.doesNotMatch(notes, /one-hour|no unbounded fork wait/);
    });
  }
}

test('every filesystem row says v2 sandbox paths are absolute, in the same words for both tools', () => {
  // Given: the 0.x filesystem rows, whose relative paths v2 refuses.
  const rows = mapping.suggestions.filter((entry) => entry.receiver === 'sandbox.fs');
  assert.ok(rows.length > 0);
  // When / Then: each language's notes carry the one shared note, and the guide shows it.
  const note = 'v2 takes only absolute sandbox paths (starting with /) and refuses a relative one before any request; v0 resolved a relative path against the sandbox working directory';
  for (const entry of rows) {
    for (const language of ['py', 'ts']) assert.ok(entry.notes[language].includes(note), `${entry.id} ${language}`);
  }
  assert.ok(mapping.receivers['sandbox.fs'].notes.includes(`Python and TypeScript: ${note}`));
  const section = guide.slice(guide.indexOf('<a id="sandbox-fs"></a>'), guide.indexOf('<a id="sandbox-git"></a>'));
  assert.ok(section.includes(note));
});

test('the Dockerfile-image forms name the 0.x call they replace', () => {
  // Given: rows with a form for a create that passes a 0.x Dockerfile image.
  const rows = mapping.suggestions.filter((entry) => entry.dockerfileImage !== undefined);
  assert.deepEqual(rows.map((entry) => entry.id).sort(), ['client.create', 'client.snapshot.create']);
  for (const entry of rows) {
    for (const language of ['py', 'ts']) {
      const { type, method } = mapping.dockerfileImage[language];
      // Then: each builds from a context and its notes name the 0.x call.
      assert.match(entry.dockerfileImage.v2[language], language === 'py' ? /\.build_from_context\(/ : /\.buildFromContext\(/);
      assert.ok(entry.dockerfileImage.notes[language].some((note) => note.includes(`${type}.${method}(path)`)));
      // And: the guide's row for this call and language shows that form.
      const receiver = entry.receiver === 'client' ? 'client' : 'client.snapshot';
      const row = guide.split('\n').find((line) => line.startsWith(`| \`${receiver}.${entry.v0[language]}(...)\` with \`${type}.${method}(...)\``));
      assert.ok(row, `${entry.id} ${language}: no guide row`);
      assert.ok(row.includes(entry.dockerfileImage.v2[language].replace(/\?/g, '')), `${entry.id} ${language}: guide row lacks the form`);
    }
  }
});
