/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Project, SyntaxKind } from 'ts-morph';
import { expect, it } from 'vitest';
import { loadMapping } from '../src/mapping.js';
import type { Report } from '../src/model.js';
import { upgrade } from '../src/upgrade.js';
import { contents, invoke, temporaryProject } from './helpers.js';

const mapping = loadMapping();
const imports = "import { Daytona, DaytonaError, DaytonaAuthenticationError, DaytonaValidationError, DaytonaAuthorizationError } from '@daytonaio/sdk';";

it.each([
  ['bare catch binding', 'catch (e) { client = undefined; }'],
  ['bare catch', 'catch { client = undefined; }'],
  ['base error', 'catch (e) { if (e instanceof DaytonaError) client = undefined; }'],
  ['built-in Error', 'catch (e) { if (e instanceof Error) client = undefined; }'],
  ['authentication error', 'catch (e) { if (e instanceof DaytonaAuthenticationError) client = undefined; }'],
  ['filtered rethrow', 'catch (e) { if (e instanceof DaytonaError) return; throw e; }'],
  // The two 0.x root aliases 2.0 dropped name shared categories too.
  ['former validation alias', 'catch (e) { if (e instanceof DaytonaValidationError) client = undefined; }'],
  ['former authorization alias', 'catch (e) { if (e instanceof DaytonaAuthorizationError) client = undefined; }'],
])('reports the constructor when protected by %s', (_name, handler) => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${imports}\nlet client;\ntry { client = new Daytona(); } ${handler}`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items).toMatchObject([{ kind: 'fixme', file: 'app.ts', line: 3 }]);
  expect(result.after).toContain(`// FIXME(daytona-upgrade): ${result.items[0]?.message}`);
  expect(result.items[0]?.message).toContain('client.v0');
});

it.each([
  ['outside try', 'const client = new Daytona();'],
  ['unrelated error', 'try { new Daytona(); } catch (e) { if (e instanceof TypeError) recover(); }'],
  ['unconditional rethrow', 'try { new Daytona(); } catch (e) { throw e; }'],
  ['logged rethrow', 'try { new Daytona(); } catch (e) { log(e); throw e; }'],
  ['different type check', 'try { new Daytona(); } catch (e) { if (typeof e === "string") recover(); }'],
  ['nested arrow', 'try { const make = () => new Daytona(); } catch {}'],
  ['nested function', 'try { function make() { return new Daytona(); } } catch {}'],
  ['nested method', 'try { const factory = { make() { return new Daytona(); } }; } catch {}'],
  ['catch body', 'try { work(); } catch { new Daytona(); }'],
  ['finally body', 'try { work(); } catch {} finally { new Daytona(); }'],
  ['no catch', 'try { new Daytona(); } finally { cleanup(); }'],
  ['nested error check only', 'try { new Daytona(); } catch (e) { const inspect = () => e instanceof DaytonaError; throw e; }'],
  ['unrelated constructor', 'try { new Other(); } catch {}'],
  ['shadowed client', 'function make(Daytona: typeof Other) { try { new Daytona(); } catch {} }'],
  ['shadowed error', 'function make(DaytonaError: typeof TypeError) { try { new Daytona(); } catch (e) { if (e instanceof DaytonaError) recover(); } }'],
  ['shadowed built-in Error', "import { Error } from './errors'; try { new Daytona(); } catch (e) { if (e instanceof Error) recover(); }"],
  ['unrelated imported error', "import { DaytonaError as Failure } from 'other'; try { new Daytona(); } catch (e) { if (e instanceof Failure) recover(); }"],
  ['v2 constructor', "import { Daytona as V2 } from '@daytona/sdk/v2'; try { new V2(); } catch {}"],
])('leaves the constructor unflagged when %s', (_name, body) => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts', `${imports}\n${body}`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items).toEqual([]);
  expect(result.after).not.toContain('FIXME(daytona-upgrade)');
});

it.each([
  ['variable', 'const integration = new Daytona();', 'integration'],
  ['assignment', 'integration = new Daytona();', 'integration'],
  ['parenthesized assignment', 'integration = (new Daytona());', 'integration'],
  ['nested statement', 'if (enabled) { const integration = new Daytona(); }', 'integration'],
  ['property assignment', 'this.integration = new Daytona();', 'this.integration'],
  ['return', 'return new Daytona();', undefined],
  ['unassigned', 'new Daytona();', undefined],
])('names the receiver when the constructor is in a %s', (_name, body, receiver) => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${imports}\nfunction make() { try { ${body} } catch {} }`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items).toHaveLength(1);
  expect(result.items[0]?.message).toContain(receiver ? `const v0 = ${receiver}.v0` : 'access .v0 on this client inside the try');
});

it.each([
  ["import { Daytona as Client, DaytonaError as Failure } from '@daytonaio/sdk';", 'Client', 'Failure'],
  ["import { Daytona } from '@daytona/sdk'; import { DaytonaError as Failure } from '@daytona/sdk/v0';", 'Daytona', 'Failure'],
  ["import * as sdk from '@daytonaio/sdk';", 'sdk.Daytona', 'sdk.DaytonaError'],
  ["import { Daytona } from '@daytona/sdk'; import * as errors from '@daytona/sdk/v0';", 'Daytona', 'errors.DaytonaAuthenticationError'],
  ["const { Daytona: Client, DaytonaError: Failure } = require('@daytonaio/sdk');", 'Client', 'Failure'],
  ["import { Daytona, DaytonaError } from '@daytonaio/sdk'; const Client = Daytona;", 'Client', 'DaytonaError'],
  ["import { Daytona, DaytonaError } from '@daytonaio/sdk'; class Client extends Daytona {}", 'Client', 'DaytonaError'],
])('recognizes proven constructors and shared errors when using %s', (header, constructor, error) => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${header}\ntry { const client = new ${constructor}(); } catch (e) { if (e instanceof ${error}) recover(); }`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items).toMatchObject([{ kind: 'fixme', line: 2 }]);
});

it.each(mapping.rootNames.ts.filter(name => /^Daytona.*Error$/.test(name)))(
  'reports the constructor when its catch checks %s from v0', name => {
    // Given
    const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
      `import { Daytona } from '@daytona/sdk'; import { ${name} as Failure } from '@daytona/sdk/v0';
try { new Daytona({ apiKey: 'test-key' }); } catch (e) { if (e instanceof Failure) recover(); }`);
    // When
    const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
    // Then
    expect(result.items).toMatchObject([{ kind: 'fixme', line: 2 }]);
  },
);

it('reports each construction when several share the same protected statement', () => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${imports}\ntry { const first = new Daytona(), second = new Daytona(); } catch {}`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items.map(item => item.kind)).toEqual(['fixme', 'fixme']);
  expect(result.after.match(/\/\/ FIXME\(daytona-upgrade\):/g)).toHaveLength(2);
});

it('reports a nested function construction when it has its own try and catch', () => {
  // Given
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${imports}\ntry { const make = () => { try { return new Daytona(); } catch {} }; } catch {}`);
  // When
  const result = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(result.items).toMatchObject([{ kind: 'fixme' }]);
});

it('keeps the FIXME and manual item when upgrading the output again', () => {
  // Given an already upgraded catch with its constructor FIXME.
  const source = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts',
    `${imports}\ntry { const integration = new Daytona(); } catch (e) { if (e instanceof DaytonaError) recover(); }`);
  const first = upgrade(source, mapping, { root: '/', dropApiUrl: false });
  const upgraded = new Project({ useInMemoryFileSystem: true }).createSourceFile('/app.ts', first.after);
  // When
  const second = upgrade(upgraded, mapping, { root: '/', dropApiUrl: false });
  // Then
  expect(second.after).toBe(first.after);
  expect(second.items).toMatchObject([{ kind: 'fixme', message: first.items[0]?.message }]);
  expect(second.after.match(/\/\/ FIXME\(daytona-upgrade\):/g)).toHaveLength(1);
  const constructor = upgraded.getFirstDescendantByKindOrThrow(SyntaxKind.NewExpression);
  expect(second.items[0]?.line).toBe(constructor.getStartLineNumber());
});

it.each([false, true])('reports the manual item through CLI JSON when strict=%s', async strict => {
  // Given an optional integration in a real project.
  const root = temporaryProject({
    'package.json': '{"dependencies":{"@daytonaio/sdk":"^0.224.0"}}',
    'app.ts': `${imports}\ntry { const client = new Daytona(); } catch { disable(); }`,
  });
  // When
  const result = await invoke(root, ['--json', ...(strict ? ['--strict'] : [])]);
  // Then
  const report: Report = JSON.parse(result.output);
  expect(result.code, result.errors).toBe(strict ? 1 : 0);
  expect(report.items.filter(item => item.kind === 'fixme')).toMatchObject([{ kind: 'fixme', file: 'app.ts', line: 2 }]);
  expect(contents(root)['app.ts']).toContain('FIXME(daytona-upgrade)');
});
