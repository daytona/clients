/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { contents, fixture, here, invoke } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

describe('CLI project fixtures', () => {
  for (const name of readdirSync(join(here, 'fixtures'))) {
    it(`produces the expected project when migrating ${name}`, async () => {
      // Given
      const root = fixture(name);
      const flags = name.startsWith('suggest-') ? ['--suggest-v2'] : name === 'remove-suggestions' ? ['--remove-suggestions'] : [];
      const expected = contents(join(here, 'fixtures', name, 'expected'));
      // When
      const result = await invoke(root, flags, name.startsWith('suggest-') ? suggestionMapping : undefined);
      // Then
      expect(result.errors).toBe('');
      expect(result.code).toBe(name.endsWith('custom-api-url-stop') ? 2 : 0);
      expect(contents(root)).toEqual(expected);
    });
    if (!name.endsWith('custom-api-url-stop')) it(`is idempotent when ${name} is already migrated`, async () => {
      // Given
      const root = fixture(name);
      const flags = name.startsWith('suggest-') ? ['--suggest-v2'] : name === 'remove-suggestions' ? ['--remove-suggestions'] : [];
      const mapping = name.startsWith('suggest-') ? suggestionMapping : undefined;
      await invoke(root, flags, mapping);
      const before = contents(root);
      // When
      const result = await invoke(root, flags, mapping);
      // Then
      expect(result.errors).toBe('');
      expect(contents(root)).toEqual(before);
    });
  }
});
