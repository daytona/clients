/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

it.each(['snapshot', 'volume', 'secret', 'warmPool'])(
  'requests review when a hinted client is stored in holder.%s', async service => {
    // Given a linked but unproven client whose property name resembles a service.
    const root = temporaryProject({
      'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'app.ts': `import { Daytona } from '@daytonaio/sdk';
function wrap(value: unknown): unknown { return value; }
const holder = { ${service}: wrap(new Daytona()) };
holder.${service}.create();
`,
    });

    // When the CLI upgrades it without SDK types.
    const result = await invoke(root, ['--json']);

    // Then the precision-first policy requests review rather than silently skipping it.
    const report: Report = JSON.parse(result.output);
    expect(result.code, result.errors).toBe(0);
    expect(report.items.filter(item => item.kind === 'fixme')).toHaveLength(1);
    expect(contents(root)['app.ts']).toContain(`holder.${service}.create();`);
    expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade):');
  },
);
