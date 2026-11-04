/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Mapping } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';
import { suggestionMapping } from './suggestion-mapping.js';

it.each(['stop', 'start', 'pause', 'archive', 'setAutostopInterval', 'setAutoPauseInterval', 'setAutoArchiveInterval'])(
  'keeps a workflow on v0 when it includes %s', async method => {
    // Given
    const mapping: Mapping = { ...suggestionMapping, suggestions: [...suggestionMapping.suggestions.filter(entry => entry.id !== `sandbox.${method}`),
      { id: `sandbox.${method}`, receiver: 'sandbox', v0: { ts: method }, v2: { ts: `{sandbox}.${method}()` },
        category: 'differs', notes: { ts: [] }, anchor: method, startsWorkflow: false }] };
    const root = temporaryProject({ 'app.ts': `import { Daytona } from '@daytona/sdk';
const d = new Daytona();
const s = await d.v0.create();
await s.${method}();
` });
    // When
    const result = await invoke(root, ['--suggest-v2'], mapping);
    // Then
    const message = `workflow \`s\`: keep this 0.x lifecycle workflow on .v0; v2 stop, start and pause apply to macOS sandboxes: ${method}`;
    expect(result.code).toBe(0);
    expect(contents(root)['app.ts']).toContain(`// daytona-v2: ${message}`);
    expect(contents(root)['app.ts']).not.toContain('move these v0 calls on it to v2 together');
  });
