/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, it } from 'vitest';
import type { Report } from '../src/model.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const services = ['snapshot', 'volume', 'secret', 'warmPool'] as const;
const serviceTypes = `
interface Service { list(): Promise<string[]>; get(name: string): Promise<string>; }
interface Services { list(): Promise<string[]>; ${services.map(name => `${name}: Service;`).join('\n')} }
`;
const fakeSdk = {
  'node_modules/@daytonaio/sdk/package.json': '{"name":"@daytonaio/sdk","version":"0.224.0","types":"index.d.ts"}',
  'node_modules/@daytonaio/sdk/index.d.ts': `${serviceTypes}
export declare class Daytona implements Services {
  list(): Promise<string[]>;
  ${services.map(name => `${name}: Service;`).join('\n')}
}`,
  'node_modules/@daytona/sdk/package.json': '{"name":"@daytona/sdk","version":"2.0.0","types":"index.d.ts"}',
  'node_modules/@daytona/sdk/index.d.ts': `${serviceTypes}
export declare class Daytona { readonly v0: Services; }`,
} as const;

it.each([false, true].flatMap(resolved => services.map(service => ({ resolved, service }))))(
  'is byte-idempotent for a class-held $service service when SDK types resolve: $resolved',
  async ({ resolved, service }) => {
    // Given a service class upgraded once through the CLI, before or after installation.
    const root = temporaryProject({
      'tsconfig.json': '{"compilerOptions":{"strict":true,"module":"NodeNext","moduleResolution":"NodeNext"}}',
      'package.json': '{"dependencies":{"@daytonaio/sdk":"0.224.0"}}',
      'service.ts': `import { Daytona } from '@daytonaio/sdk';
export class Service {
  private daytona: Daytona;
  constructor() { this.daytona = new Daytona(); }
  list() { return this.daytona.${service}.list(); }
  get() { return this.daytona.${service}.get('name'); }
}
const service = new Service();
service.list();
service.get();
`,
      ...(resolved ? fakeSdk : {}),
    });
    const first = await invoke(root, ['--json']);
    expect(first.code, first.errors).toBe(0);
    const firstReport: Report = JSON.parse(first.output);
    expect(firstReport.items.filter(item => item.kind === 'fixme')).toEqual([]);
    const before = contents(root);
    expect(before['service.ts']).toContain(`this.daytona.v0.${service}.list()`);
    expect(before['service.ts']).toContain(`this.daytona.v0.${service}.get('name')`);

    // When the same CLI runs again without changing dependencies.
    const second = await invoke(root, ['--json']);

    // Then no bytes, files, or review items are added on the second run.
    const secondReport: Report = JSON.parse(second.output);
    expect(second.code, second.errors).toBe(0);
    expect(contents(root)).toEqual(before);
    expect(secondReport.changedFiles).toEqual([]);
    expect(secondReport.items).toEqual(firstReport.items);
  },
);

it.each([false, true])('leaves explicit v0 access paths clean when SDK types resolve: %s', async resolved => {
  // Given namespaced clients with direct, nested, computed, and optional access paths.
  const source = `import { Daytona } from '@daytona/sdk';
export class Service {
  private daytona: Daytona;
  constructor() { this.daytona = new Daytona(); }
  run() {
    this.daytona.v0.list();
${services.map(name => `this.daytona.v0.${name}.list();
this.daytona['v0']['${name}'].get('name');
(this.daytona?.v0?.${name})?.list();`).join('\n')}
  }
}
const service = new Service();
service.run();
`;
  const root = temporaryProject({
    'tsconfig.json': '{"compilerOptions":{"strict":true,"module":"NodeNext","moduleResolution":"NodeNext"}}',
    'package.json': '{"dependencies":{"@daytona/sdk":"^2.0.0"}}',
    'service.ts': source,
    ...(resolved ? fakeSdk : {}),
  });

  // When the CLI examines code that already chose v0.
  const result = await invoke(root, ['--json']);

  // Then it neither rewrites the source nor requests a root-client migration.
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.errors).toBe(0);
  expect(contents(root)['service.ts']).toBe(source);
  expect(report.changedFiles).toEqual([]);
  expect(report.items.filter(item => item.kind === 'fixme')).toEqual([]);
  expect(report.items.some(item => item.kind === 'types-unresolved')).toBe(!resolved);
});
