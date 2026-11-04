/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { installFakeSdk, invoke, temporaryProject } from './helpers.js';

it.each(['local-export', 'import', 're-export', 'export-specifier'])('suggests five client methods when binding uses %s', async layout => {
  // Given
  const constructor = "import { Daytona } from '@daytonaio/sdk';\n";
  const files: Record<string, string> = layout === 'local-export' ? {} : {
    'lib/daytona.ts': constructor + (layout === 'export-specifier' ?
      'const original = new Daytona(); export { original as daytona };' : 'export const daytona = new Daytona();'),
  };
  if (layout === 're-export') files['lib/index.ts'] = "export { daytona as client } from './daytona.js';";
  files['app/route.ts'] = (layout === 'local-export' ? constructor + 'export const daytona = new Daytona();' :
    layout === 're-export' ? "import { client as daytona } from '../lib/index.js';" : "import { daytona } from '../lib/daytona.js';") + `
export async function GET() {
  const sandbox = await daytona.create();
  await daytona.get('id');
  await daytona.list();
  await daytona.start(sandbox);
  await daytona.stop(sandbox);
}`;
  const root = temporaryProject(files);
  installFakeSdk(root);
  // When
  const result = await invoke(root, ['--suggest-v2', '--json']);
  // Then
  const items: { kind: string; file: string }[] = JSON.parse(result.output).items;
  expect(items.filter(item => item.kind === 'suggestion' && item.file === 'app/route.ts'), JSON.stringify(items)).toHaveLength(5);
  expect(items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(items.filter(item => item.kind === 'workflow')).toHaveLength(1);
});
