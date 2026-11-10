/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const mapping = JSON.parse(readFileSync(new URL('../mapping/mapping.json', import.meta.url), 'utf8'));
const guide = readFileSync(new URL('../MIGRATION.md', import.meta.url), 'utf8');
const guideAnchors = new Set([...guide.matchAll(/<a id="([^"]+)"><\/a>/g)].map((match) => match[1]));
const inRepoGuide = 'https://github.com/daytona/clients/blob/main/upgrade/MIGRATION.md';

test('the docs base is a page, not the docs home', () => {
  // Given: the base every suggestion link starts with.
  const { base, placeholder } = mapping.docs;
  // Then: it is the in-repository guide until the docs site publishes one,
  // never the docs home, where the section anchors do not exist.
  assert.equal(placeholder, false);
  assert.doesNotMatch(base, /^https:\/\/www\.daytona\.(com|io)\/docs\/?$/);
  assert.equal(base, inRepoGuide, 'a published guide page needs its anchors checked here before it replaces the in-repo guide');
});

test('every suggestion anchor exists in the generated guide', () => {
  // Given: each anchor a suggestion or a workflow header links to.
  const anchors = new Set(['data', ...mapping.suggestions.map((entry) => entry.anchor)]);
  // Then: the guide the links point at carries an element with that id.
  const missing = [...anchors].filter((anchor) => !guideAnchors.has(anchor));
  assert.deepEqual(missing, []);
});
