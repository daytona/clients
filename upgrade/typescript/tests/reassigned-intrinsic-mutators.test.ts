/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import { contents, installFakeSdk, invoke, temporaryProject } from './helpers.js';

const prefix = "import { Daytona } from '@daytonaio/sdk';\n";
const setup = "const local = {create() { return 'local'; }}; const service = {client: new Daytona()};\n";
const programs = [
  "const R = Reflect; R.set = (target, _key, value) => { target.client = value; return true; }; function replace(target) { R.set(target, 'other', local); } replace(service);",
  "const O = Object; O.defineProperty = (target, _key, d) => { target.client = d.value; return target; }; function replace(target) { O.defineProperty(target, 'other', {value: local}); } replace(service);",
] as const;

it.each([false, true].flatMap(types => programs.map(program => ({ types, program }))))(
  'does not trust a reassigned intrinsic alias with types=$types: $program', async ({ types, program }) => {
    // Given an opaque target and a Reflect/Object alias whose mutator was replaced.
    const root = temporaryProject({ 'app.js': prefix + setup + program + '\nservice.client.create();\n' });
    if (types) installFakeSdk(root);
    // When
    const result = await invoke(root, ['--json']);
    // Then the replaced member may have swapped the client, so the call is reviewed, not rewritten.
    expect(result.code).toBe(0);
    expect(contents(root)['app.js']).not.toContain('service.client.v0.create()');
    expect(JSON.parse(result.output).items).toContainEqual(expect.objectContaining({ kind: 'fixme' }));
  });
