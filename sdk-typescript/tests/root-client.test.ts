/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawnSync } from 'node:child_process'
import { createServer } from 'node:http'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { inspect } from 'node:util'

import { Daytona, DEFAULT_V2_API_URL, MOVED_TO_V0, ROOT_CONFIG_ROUTING, TARGET_DEPRECATION, V0_API_URL, resetTargetDeprecationWarningForTests, setV0ModuleLoaderForTests } from '../src/Daytona'
import { Daytona as DaytonaV2 } from '../src/v2/Daytona'
import * as rootModule from '../src/index'
import { SDK_VERSION } from '../src/version'
import {
  DaytonaBadRequestError,
  DaytonaConflictError,
  DaytonaError,
  DaytonaForbiddenError,
  DaytonaNotFoundError,
  DaytonaTimeoutError,
  ERROR_REGISTRY_KEY,
} from '../src/errors'
import { NotFoundError, NotSupportedError, RequestTimeoutError, ClientTimeoutError } from '../src/v2/errors/DaytonaError'

class FakeV0DaytonaError extends Error {}
class FakeV0DaytonaNotFoundError extends FakeV0DaytonaError {}

class FakeV0SnapshotNamespace {
  public readonly list = jest.fn(async function list(this: FakeV0SnapshotNamespace) {
    return this
  })
}

class FakeV0Daytona {
  public static readonly instances: FakeV0Daytona[] = []
  public readonly snapshot = new FakeV0SnapshotNamespace()
  public readonly list = jest.fn(async function list(this: FakeV0Daytona) {
    return this
  })

  constructor(public readonly config: Record<string, unknown>) {
    FakeV0Daytona.instances.push(this)
  }
}

afterEach(() => {
  jest.restoreAllMocks()
  process.env.DAYTONA_V2_API_URL = 'http://api.invalid'
  process.env.DAYTONA_API_URL = 'http://never-read-by-2x.invalid'
  delete process.env.DAYTONA_V0_API_URL
  FakeV0Daytona.instances.length = 0
})

/** Node's Symbol.asyncDispose (absent inside jest's vm context, same registry symbol). */
const ASYNC_DISPOSE: symbol = (Symbol as { readonly asyncDispose?: symbol }).asyncDispose ?? Symbol.for('nodejs.asyncDispose')

/** A v0 client fixture with a synchronous iterator factory and async services. */
class IteratorV0Daytona {
  public readonly snapshot = {
    list: async function list(this: { readonly owner: IteratorV0Daytona }) {
      return { items: [`snap-of-${this.owner.config.apiKey}`] }
    },
    owner: this,
  }
  public readonly volume = { get: async (name: string) => ({ name }) }
  public listed = 0

  constructor(public readonly config: Record<string, unknown>) {}

  public list(query?: { readonly labels?: Record<string, string> }): AsyncIterableIterator<string> {
    this.listed++
    const prefix = query?.labels?.team ?? 'sbx'
    return (async function* () {
      yield `${prefix}-1`
      yield `${prefix}-2`
    })()
  }
}

function missingV0Entry(): Error {
  return Object.assign(new Error("Cannot find module '@daytona/sdk/v0'"), { code: 'MODULE_NOT_FOUND' })
}

describe('root Daytona client', () => {
  it('builds and caches v2 lazily with the default API URL', () => {
    delete process.env.DAYTONA_V2_API_URL
    const root = new Daytona({ apiKey: 'key' })

    expect('create' in root).toBe(false)

    const first = root.v2
    const second = root.v2

    expect(first).toBeInstanceOf(DaytonaV2)
    expect(first).toBe(second)
    expect(first['apiKey']).toBe('key')
    expect(first['api'].defaults.baseURL).toBe(DEFAULT_V2_API_URL)
  })

  it('close() drops the cached v2 client, so later waits get a live events hub again', async () => {
    const root = new Daytona({ apiKey: 'key' })
    const hubOf = (v2: DaytonaV2) => (v2 as unknown as { acquireHub(): unknown }).acquireHub()
    const first = root.v2
    expect(hubOf(first)).toBeDefined()
    await root.close()
    expect(hubOf(first)).toBeUndefined() // the closed client never hands out a hub
    const second = root.v2
    expect(second).not.toBe(first)
    expect(hubOf(second)).toBeDefined()
    await root.close()
    await expect(new Daytona({ apiKey: 'key' }).close()).resolves.toBeUndefined() // nothing built
  })

  it('close() disposes a created v0 client, does nothing for v0 never accessed, and a later root.v0 is fresh', async () => {
    const disposed: DisposableV0[] = []
    class DisposableV0 extends IteratorV0Daytona {
      async [ASYNC_DISPOSE](): Promise<void> {
        disposed.push(this)
      }
    }
    const loader = jest.fn(() => ({ RootOnlyClientV0: DisposableV0 }))
    const untouched = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), loader)
    await untouched.close()
    expect(loader).not.toHaveBeenCalled()

    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), loader)
    const first = root.v0 as unknown as DisposableV0
    await root.close()
    expect(disposed).toEqual([first])
    const second = root.v0 as unknown as DisposableV0
    expect(second).not.toBe(first)
    await root.close()
    expect(disposed).toEqual([first, second])
    await root.close() // idempotent: nothing left to dispose
    expect(disposed).toHaveLength(2)
  })

  it('supports await using: Symbol.asyncDispose closes the root', async () => {
    const root = new Daytona({ apiKey: 'key' })
    const first = root.v2
    const close = jest.spyOn(root, 'close')
    // Same key the class uses (as unit.test.ts does for Sandbox / Lsp).
    await root[Symbol.asyncDispose]()
    expect(close).toHaveBeenCalledTimes(1)
    expect(root.v2).not.toBe(first)
  })

  it('close() closes both namespaces even if one fails, and re-throws the first error', async () => {
    const disposed: unknown[] = []
    class DisposableV0 extends IteratorV0Daytona {
      async [ASYNC_DISPOSE](): Promise<void> {
        disposed.push(this)
      }
    }
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), () => ({ RootOnlyClientV0: DisposableV0 }))
    void root.v0
    const v2 = root.v2
    jest.spyOn(v2, 'close').mockRejectedValueOnce(new Error('v2 close failed'))
    await expect(root.close()).rejects.toThrow('v2 close failed')
    expect(disposed).toHaveLength(1)
  })

  it.each(['', '   ', '\t\n'])('treats a blank or whitespace DAYTONA_V2_API_URL (%j) as unset', (blank) => {
    process.env.DAYTONA_V2_API_URL = blank
    const v2 = new DaytonaV2({ apiKey: 'key' })

    expect(v2['api'].defaults.baseURL).toBe(DEFAULT_V2_API_URL)
  })

  it('defaults the v2 client itself to DEFAULT_V2_API_URL instead of requiring apiUrl', () => {
    delete process.env.DAYTONA_V2_API_URL
    const v2 = new DaytonaV2({ apiKey: 'key' })

    expect(v2['api'].defaults.baseURL).toBe(DEFAULT_V2_API_URL)
  })

  it('treats direct root instances as native matches', () => {
    expect(new DaytonaNotFoundError('missing')).toBeInstanceOf(DaytonaError)
    expect(new DaytonaNotFoundError('missing')).toBeInstanceOf(DaytonaNotFoundError)
  })

  it('exports exactly the twelve shared categories and the base, with no synonyms', () => {
    const rootErrors = Object.keys(rootModule).filter((name) => name.endsWith('Error')).sort()
    expect(rootErrors).toEqual([
      'DaytonaAuthenticationError',
      'DaytonaBadRequestError',
      'DaytonaConflictError',
      'DaytonaConnectionError',
      'DaytonaError',
      'DaytonaForbiddenError',
      'DaytonaGoneError',
      'DaytonaInternalServerError',
      'DaytonaNotFoundError',
      'DaytonaRateLimitError',
      'DaytonaServiceUnavailableError',
      'DaytonaTimeoutError',
      'DaytonaUnprocessableEntityError',
    ])
  })

  it('takes only the configuration in its published constructor', () => {
    expect(Daytona.length).toBe(1)
    const declarations = readFileSync(join(__dirname, '..', 'src', 'Daytona.ts'), 'utf8')
    expect(declarations).toMatch(/constructor\(config\?: DaytonaConfig\)/)
  })

  it('catches errors a separately loaded v0 copy registered on globalThis, with no d.v0 access', () => {
    // What the bundled v0 entry appends (scripts/bundle-v0.mjs): it registers
    // its own classes, by category name, into the registry the root reads.
    class SeparateV0NotFoundError extends Error {}
    const holder = globalThis as unknown as Record<symbol, Map<string, Function[]> | undefined>
    const registry = (holder[ERROR_REGISTRY_KEY] ??= new Map<string, Function[]>())
    registry.set('DaytonaNotFoundError', [...(registry.get('DaytonaNotFoundError') ?? []), SeparateV0NotFoundError])
    registry.set('DaytonaError', [...(registry.get('DaytonaError') ?? []), SeparateV0NotFoundError])
    const error = new SeparateV0NotFoundError('v0 missing')
    expect(error).toBeInstanceOf(DaytonaNotFoundError)
    expect(error).toBeInstanceOf(DaytonaError)
    expect(error).not.toBeInstanceOf(DaytonaConflictError)
    expect(ERROR_REGISTRY_KEY).toBe(Symbol.for('@daytona/sdk/error-registry'))
  })

  it('registers each root class in its own category, so another copy\'s root markers match', () => {
    const holder = globalThis as unknown as Record<symbol, Map<string, Function[]> | undefined>
    const registry = holder[ERROR_REGISTRY_KEY]
    expect(registry?.get('DaytonaNotFoundError')).toContain(DaytonaNotFoundError)
    expect(registry?.get('DaytonaError')).toContain(DaytonaError)
    // The other (ESM/CJS) copy's root class registers the same way.
    class OtherCopyRootNotFound extends Error {}
    registry?.set('DaytonaNotFoundError', [...(registry.get('DaytonaNotFoundError') ?? []), OtherCopyRootNotFound])
    expect(new OtherCopyRootNotFound()).toBeInstanceOf(DaytonaNotFoundError)
    expect(new DaytonaError('x')).not.toBeInstanceOf(DaytonaNotFoundError)
  })

  it('maps v2 error instances onto the shared root hierarchy', () => {
    expect(new NotFoundError('missing', { statusCode: 404, code: 'NOT_FOUND', source: 'SDK' })).toBeInstanceOf(DaytonaError)
    expect(new NotFoundError('missing', { statusCode: 404, code: 'NOT_FOUND', source: 'SDK' })).toBeInstanceOf(DaytonaNotFoundError)
    expect(new NotFoundError('missing', { statusCode: 404, code: 'NOT_FOUND', source: 'SDK' })).not.toBeInstanceOf(DaytonaConflictError)
    expect(new ClientTimeoutError('timed out', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })).toBeInstanceOf(DaytonaTimeoutError)
    expect(new RequestTimeoutError('timed out', { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })).toBeInstanceOf(DaytonaTimeoutError)
  })

  it('reads DAYTONA_V2_API_URL for v2 and passes JWT exchange inputs through', () => {
    process.env.DAYTONA_V2_API_URL = 'https://preview.example'

    const v2 = new Daytona({ jwtToken: 'jwt', projectId: 'project-id', requestTimeoutSeconds: 12.345 }).v2

    expect(v2['api'].defaults.baseURL).toBe('https://preview.example')
    expect(v2['httpOptions'].authProvider).toBeDefined()
    expect(v2['apiTransport'].defaultTimeout).toBe(12_345)
  })

  it('warns once that config target is deprecated, and stays silent without it', () => {
    resetTargetDeprecationWarningForTests()
    const emit = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    try {
      new Daytona({ apiKey: 'key' })
      expect(emit).not.toHaveBeenCalled()
      new Daytona({ apiKey: 'key', target: 'eu' })
      new Daytona({ apiKey: 'key', target: 'us' })
      expect(emit).toHaveBeenCalledTimes(1)
      expect(emit).toHaveBeenCalledWith(TARGET_DEPRECATION, { type: 'DeprecationWarning', code: 'DAYTONA_CONFIG_TARGET' })
      expect(TARGET_DEPRECATION).toContain('applies only to the v0 namespace')
      expect(TARGET_DEPRECATION).toContain('d.v0.create({ target })')
      expect(TARGET_DEPRECATION).toContain('regionId')
      // The latch is process-global, shared by the ESM and CJS copies.
      const holder = globalThis as unknown as Record<symbol, boolean | undefined>
      expect(holder[Symbol.for('@daytona/sdk/target-deprecation-warned')]).toBe(true)
    } finally {
      emit.mockRestore()
      resetTargetDeprecationWarningForTests()
    }
  })

  it('forwards exactly the root-config fields routed to v0 (parity with Python)', () => {
    const emit = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    const root = setV0ModuleLoaderForTests(new Daytona({
        apiKey: 'key',
        organizationId: 'org',
        target: 'eu',
        projectId: 'proj',
        requestTimeoutSeconds: 5,
        otelEnabled: true,
        connectionPoolMaxsize: 4,
      }), () => ({ RootOnlyClientV0: FakeV0Daytona }))
    void root.v0
    emit.mockRestore()
    const instance = FakeV0Daytona.instances[0]
    if (!instance) throw new Error('expected a fake v0 instance')
    const forwarded = new Set(Object.keys(instance.config))
    for (const [field, route] of Object.entries(ROOT_CONFIG_ROUTING)) {
      if (field === 'jwtToken') continue // set together with apiKey is invalid; covered by the mapping test
      if (field === 'otelApi') continue // a module handle, v2-only
      expect([field, forwarded.has(field)]).toEqual([field, route !== 'v2'])
    }
    expect(instance.config.otelEnabled).toBe(true)
  })

  it('d.v0 is the real v0 client: loaded synchronously on first access, the same instance after', () => {
    process.env.DAYTONA_API_URL = 'https://ignored.example'
    const loader = jest.fn(() => ({ RootOnlyClientV0: FakeV0Daytona }))
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key', jwtToken: 'jwt', organizationId: 'org-id', target: 'test-target' }), loader)
    expect(loader).not.toHaveBeenCalled() // never at construction

    const first = root.v0
    expect(first).toBeInstanceOf(FakeV0Daytona)
    expect(root.v0).toBe(first)
    // Nested services are v0's own objects, not stand-ins.
    expect(root.v0.snapshot).toBe((first as unknown as FakeV0Daytona).snapshot)
    expect(root.v0.snapshot).toBeInstanceOf(FakeV0SnapshotNamespace)
    expect(loader).toHaveBeenCalledTimes(1)
    expect(FakeV0Daytona.instances).toHaveLength(1)
    expect(FakeV0Daytona.instances[0]?.config).toEqual({
      apiKey: 'key',
      jwtToken: 'jwt',
      organizationId: 'org-id',
      target: 'test-target',
      apiUrl: V0_API_URL,
    })
  })

  it('keeps v0 semantics: sync iterator methods, async services, detached methods as in 0.x', async () => {
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), () => ({ RootOnlyClientV0: IteratorV0Daytona }))
    const items: string[] = []
    for await (const sandbox of root.v0.list({ labels: { team: 'a' } })) items.push(String(sandbox))
    expect(items).toEqual(['a-1', 'a-2'])
    await expect(root.v0.snapshot.list()).resolves.toEqual({ items: ['snap-of-key'] })
    await expect(root.v0.volume.get('data')).resolves.toEqual({ name: 'data' })
    expect(String(root.v0)).toBe('[object Object]')
    expect(inspect(root.v0)).toContain('IteratorV0Daytona')
    expect(Object.getPrototypeOf(root.v0)).toBe(IteratorV0Daytona.prototype) // v0's own object, no wrapper
  })

  it('registers v0 error classes by name when the v0 namespace loads', () => {
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), () => ({
        RootOnlyClientV0: FakeV0Daytona,
        DaytonaError: FakeV0DaytonaError,
        DaytonaNotFoundError: FakeV0DaytonaNotFoundError,
      }))
    void root.v0

    expect(new FakeV0DaytonaError('base')).toBeInstanceOf(DaytonaError)
    expect(new FakeV0DaytonaNotFoundError('missing')).toBeInstanceOf(DaytonaError)
    expect(new FakeV0DaytonaNotFoundError('missing')).toBeInstanceOf(DaytonaNotFoundError)
    expect(new FakeV0DaytonaNotFoundError('missing')).not.toBeInstanceOf(DaytonaConflictError)
  })

  it('reports other v0 load failures as what they are, not as a missing build', () => {
    const broken = setV0ModuleLoaderForTests(new Daytona({}), () => { throw new SyntaxError('Unexpected token in bundle') })
    const error = (() => {
      try {
        return broken.v0
      } catch (caught: unknown) {
        return caught
      }
    })()
    expect(error).toBeInstanceOf(DaytonaError)
    expect(error).toMatchObject({ message: 'importing the v0 namespace failed: Unexpected token in bundle', source: 'SDK' })
    expect((error as { code?: string }).code).toBeUndefined()

    const missingDependency = Object.assign(new Error("Cannot find module 'socket.io-client'"), { code: 'MODULE_NOT_FOUND' })
    const partial = setV0ModuleLoaderForTests(new Daytona({}), () => { throw missingDependency })
    expect(() => partial.v0).toThrow("importing the v0 namespace failed: Cannot find module 'socket.io-client'")

    // CJS appends a require stack naming the bundled v0 files: still a load error.
    const withStack = Object.assign(
      new Error("Cannot find module 'socket.io-client'\nRequire stack:\n- /app/node_modules/@daytona/sdk/dist/cjs/v0/Daytona.js"),
      { code: 'MODULE_NOT_FOUND' },
    )
    const stacked = setV0ModuleLoaderForTests(new Daytona({}), () => { throw withStack })
    expect(() => stacked.v0).toThrow(expect.objectContaining({ source: 'SDK' }) as unknown as Error)
    try {
      void stacked.v0
    } catch (caught: unknown) {
      expect(caught).not.toMatchObject({ code: 'NOT_IMPLEMENTED' })
    }
  })

  it('throws NotSupportedError on first access when the v0 bundle is missing, and retries later', () => {
    const loader = jest
      .fn<{ readonly [name: string]: unknown }, []>()
      .mockImplementationOnce(() => {
        throw missingV0Entry()
      })
      .mockReturnValue({ RootOnlyClientV0: FakeV0Daytona })
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), loader)

    let error: unknown
    try {
      void root.v0
    } catch (caught: unknown) {
      error = caught
    }
    expect(error).toBeInstanceOf(NotSupportedError)
    expect(error).toBeInstanceOf(DaytonaError)
    expect(error).toMatchObject({
      message: 'The v0 namespace is not included in this build of @daytona/sdk',
      statusCode: 501,
      code: 'NOT_IMPLEMENTED',
      source: 'SDK',
    })
    expect(root.v0).toBeInstanceOf(FakeV0Daytona) // not cached: the next access retries
    expect(loader).toHaveBeenCalledTimes(2)

    // ESM names the missing file exports["./v0"] points at, not the specifier.
    const fileMissing = Object.assign(new Error("Cannot find module '/app/node_modules/@daytona/sdk/dist/cjs/v0/index.js' imported from /app/x.mjs"), { code: 'ERR_MODULE_NOT_FOUND' })
    const esm = setV0ModuleLoaderForTests(new Daytona({}), () => { throw fileMissing })
    expect(() => esm.v0).toThrow(expect.objectContaining({ code: 'NOT_IMPLEMENTED' }) as unknown as Error)
  })

  it('throws NotSupportedError when ./v0 is the unbundled stub', () => {
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), () => ({ __daytonaV0Unbundled: true, RootOnlyClientV0: FakeV0Daytona }))
    expect(() => root.v0).toThrow(NotSupportedError)
    expect(FakeV0Daytona.instances).toHaveLength(0)
  })

  it('reads the client class only under its private name', () => {
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), () => ({ Daytona: FakeV0Daytona }))
    expect(() => root.v0).toThrow(expect.objectContaining({ code: 'NOT_IMPLEMENTED' }) as unknown as Error)
    expect(FakeV0Daytona.instances).toHaveLength(0)
  })

  it("surfaces v0's own constructor errors unchanged", () => {
    class RejectingV0Daytona {
      constructor() {
        throw new Error('API key or JWT token is required')
      }
    }
    const root = setV0ModuleLoaderForTests(new Daytona({}), () => ({ RootOnlyClientV0: RejectingV0Daytona }))

    expect(() => root.v0).toThrow('API key or JWT token is required')
    try {
      void root.v0
    } catch (caught: unknown) {
      expect(caught).not.toBeInstanceOf(DaytonaError)
    }
  })

  it('types d.v0 as the 0.x SDK client for stored client and service references', () => {
    type DaytonaV0 = InstanceType<typeof import('@daytona/sdk/v0').RootOnlyClientV0>
    const asV0 = (root: Daytona): DaytonaV0 => root.v0
    const volumeOf = (root: Daytona): DaytonaV0['volume'] => root.v0.volume
    expect([typeof asV0, typeof volumeOf]).toEqual(['function', 'function'])
  })

  it('the default loader keeps v0 unloaded for v2-only use', () => {
    // Exercised against the real package in the tarball smoke; here: the
    // default #v0-loader is resolved but never called by construction or v2.
    const root = new Daytona({ apiKey: 'key' })
    void root.v2
    expect(root['v0Client']).toBeUndefined()
  })
})

describe('root rejects URL options from untyped callers', () => {
  it.each(['apiUrl', 'toolboxUrl'])('constructor rejects %s inherited through the prototype too', (option) => {
    const config = Object.create({ [option]: 'https://self-hosted.example/api' }) as Record<string, unknown>
    config.apiKey = 'key'
    expect(() => new Daytona(config as unknown as ConstructorParameters<typeof Daytona>[0])).toThrow(`does not take '${option}'`)
  })

  it.each(['apiUrl', 'toolboxUrl'])('constructor rejects %s instead of ignoring it', (option) => {
    const config = { apiKey: 'key', [option]: 'https://self-hosted.example/api' }
    expect(() => new Daytona(config as unknown as ConstructorParameters<typeof Daytona>[0])).toThrow(
      `DaytonaConfig does not take '${option}': SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x.`,
    )
  })
})

describe('v2 rejects unsupported options from untyped callers', () => {
  it.each([
    ['apiUrl', "the v2 client does not take 'apiUrl': set DAYTONA_V2_API_URL to select an API endpoint"],
    ['target', "does not take 'target'"],
    ['toolboxUrl', "does not take 'toolboxUrl'"],
  ])('constructor rejects %s', (option, message) => {
    expect(() => new DaytonaV2({ apiKey: 'key', [option]: 'x' } as unknown as ConstructorParameters<typeof DaytonaV2>[0])).toThrow(message)
  })

  it('constructor and create reject unsupported options inherited through the prototype', async () => {
    const config = Object.create({ apiUrl: 'x' }) as Record<string, unknown>
    config.apiKey = 'key'
    expect(() => new DaytonaV2(config as unknown as ConstructorParameters<typeof DaytonaV2>[0])).toThrow("does not take 'apiUrl'")
    const v2 = new DaytonaV2({ apiKey: 'key' })
    await expect(v2.create('x', Object.create({ target: 'eu' }) as Record<string, never>)).rejects.toThrow("v2 does not take 'target'")
  })

  it.each(['create', 'createFromSnapshot', 'createMacos', 'findByName'] as const)(
    '%s rejects target instead of silently using the default region',
    async (method) => {
      const v2 = new DaytonaV2({ apiKey: 'key' })
      const call = v2[method] as unknown as (first: string, options: Record<string, unknown>) => Promise<unknown>
      await expect(call.call(v2, 'x', { target: 'eu' })).rejects.toMatchObject({ statusCode: 400, code: 'BAD_REQUEST', source: 'SDK', message: "v2 does not take 'target'; pass 'region' instead" })
    },
  )

})

describe('build:v0 without a bundle', () => {
  const packageRoot = join(__dirname, '..')
  const runBuildV0 = (env: Record<string, string>) =>
    spawnSync(process.execPath, [join(packageRoot, 'scripts', 'build-v0.mjs')], {
      cwd: packageRoot,
      encoding: 'utf8',
      env: { ...process.env, ...env },
    })
  const unbundled = !existsSync(join(packageRoot, 'src', 'v0', 'index.ts'))

  ;(unbundled ? it : it.skip)('emits the unbundled stub as ./v0 for local builds', () => {
    const result = runBuildV0({ DAYTONA_REQUIRE_V0_BUNDLE: '' })
    expect(result.status).toBe(0)
    expect(result.stdout).toContain('emitted the unbundled stub')
    expect(existsSync(join(packageRoot, 'dist', 'cjs', 'v0', 'index.js'))).toBe(true)
    expect(existsSync(join(packageRoot, 'dist', 'cjs', 'v0', 'index.d.ts'))).toBe(true)
    expect(existsSync(join(packageRoot, 'dist', 'esm', 'v0', 'index.js'))).toBe(true)
  })

  ;(unbundled ? it : it.skip)('fails in a publish build (DAYTONA_REQUIRE_V0_BUNDLE=1)', () => {
    const result = runBuildV0({ DAYTONA_REQUIRE_V0_BUNDLE: '1' })
    expect(result.status).toBe(1)
    expect(result.stderr).toContain('DAYTONA_REQUIRE_V0_BUNDLE=1')
  })
})

describe('v2 error classes across loaded copies', () => {
  it('registers every exported v2 error class for cross-copy instanceof', async () => {
    const errors = await import('../src/v2/errors/DaytonaError')
    const registry = (globalThis as unknown as Record<symbol, Map<string, Function[]> | undefined>)[
      Symbol.for('@daytona/sdk/v2-error-classes')
    ]
    if (!registry) throw new Error('v2 error registry missing')
    const registered = new Set([...registry.values()].flat())
    const errorClasses = (Object.values(errors) as unknown[]).filter(
      (value): value is Function => typeof value === 'function' && value.prototype instanceof Error,
    )
    expect(errorClasses.length).toBeGreaterThan(80)
    for (const errorClass of errorClasses) expect([errorClass.name, registered.has(errorClass)]).toEqual([errorClass.name, true])
  })

  it('matches a same-key class of another copy, natively for user subclasses', async () => {
    const { NotFoundError, ConflictError, registerV2ErrorClass, DaytonaError: V2DaytonaError } = await import(
      '../src/v2/errors/DaytonaError'
    )
    // A second copy of the module: unrelated classes registered under the same keys.
    class OtherCopyNotFoundError extends Error {}
    registerV2ErrorClass('NotFoundError', OtherCopyNotFoundError as unknown as typeof NotFoundError)
    const foreign = new OtherCopyNotFoundError('from the other copy')
    expect(foreign).toBeInstanceOf(NotFoundError)
    expect(foreign).not.toBeInstanceOf(ConflictError)
    class UserError extends NotFoundError {}
    expect(new UserError('mine')).toBeInstanceOf(NotFoundError)
    expect(new UserError('mine')).toBeInstanceOf(V2DaytonaError)
    expect(foreign).not.toBeInstanceOf(UserError)
  })
})

describe('per-namespace API URL env vars (dev / non-prod)', () => {
  /** A local server answering the v2 list; returns its base URL and the paths it saw. */
  async function listServer(): Promise<{ url: string; seen: string[]; close: () => Promise<void> }> {
    const seen: string[] = []
    const server = createServer((req, res) => {
      seen.push(req.url ?? '')
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ sandboxes: [], next_cursor: null, total: 0, total_relation: 'eq' }))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    const port = typeof address === 'object' && address ? address.port : 0
    return { url: `http://127.0.0.1:${port}`, seen, close: () => new Promise((resolve) => server.close(() => resolve())) }
  }

  it('v2 sends to DAYTONA_V2_API_URL and never to DAYTONA_API_URL', async () => {
    const server = await listServer()
    try {
      process.env.DAYTONA_V2_API_URL = server.url
      process.env.DAYTONA_API_URL = 'http://v0-sdk.invalid'
      await new Daytona({ apiKey: 'key' }).v2.list()
      expect(server.seen.some((path) => path.startsWith('/sandboxes'))).toBe(true)
    } finally {
      await server.close()
    }
  })

  it.each([undefined, '   ', '//'])('v2 does not fall back to DAYTONA_API_URL when DAYTONA_V2_API_URL is %j', (v2Url) => {
    if (v2Url === undefined) delete process.env.DAYTONA_V2_API_URL
    else process.env.DAYTONA_V2_API_URL = v2Url
    process.env.DAYTONA_API_URL = 'http://v0-sdk.invalid/'
    expect(new DaytonaV2({ apiKey: 'key' })['api'].defaults.baseURL).toBe(DEFAULT_V2_API_URL)
  })

  it('v2 strips trailing slashes from DAYTONA_V2_API_URL', () => {
    process.env.DAYTONA_V2_API_URL = 'http://v2.invalid//'
    expect(new DaytonaV2({ apiKey: 'key' })['api'].defaults.baseURL).toBe('http://v2.invalid')
  })

  it('v2 falls back to DEFAULT_V2_API_URL when both are blank', () => {
    process.env.DAYTONA_V2_API_URL = ' '
    process.env.DAYTONA_API_URL = '\t'
    expect(new DaytonaV2({ apiKey: 'key' })['api'].defaults.baseURL).toBe(DEFAULT_V2_API_URL)
  })

  it.each([
    [{ DAYTONA_V0_API_URL: 'http://v0.invalid/api', DAYTONA_API_URL: 'http://v2.invalid' }, 'http://v0.invalid/api'],
    [{ DAYTONA_V0_API_URL: '  ', DAYTONA_API_URL: 'http://v2.invalid' }, V0_API_URL],
    [{ DAYTONA_API_URL: 'http://v2.invalid' }, V0_API_URL],
    [{ DAYTONA_V0_API_URL: ' http://v0.invalid/api// ' }, 'http://v0.invalid/api'],
    [{ DAYTONA_V0_API_URL: '/' }, V0_API_URL],
  ])('v0 honours DAYTONA_V0_API_URL and ignores DAYTONA_API_URL (%j)', async (env, expected) => {
    Object.assign(process.env, env)
    const root = setV0ModuleLoaderForTests(new Daytona({ apiKey: 'key' }), () => ({ RootOnlyClientV0: FakeV0Daytona }))
    void root.v0
    expect(FakeV0Daytona.instances[0]?.config.apiUrl).toBe(expected)
  })
})


describe('the v2 namespace does not export its client', () => {
  it('exports no Daytona, DaytonaV2 or DaytonaConfig value; the root builds the client', async () => {
    const v2 = (await import('../src/v2/index')) as Record<string, unknown>
    expect(v2.Daytona).toBeUndefined()
    expect(v2.DaytonaV2).toBeUndefined()
    expect(v2.DaytonaConfig).toBeUndefined()
    expect(new Daytona({ apiKey: 'key' }).v2).toBeInstanceOf(DaytonaV2)
  })
})

describe('0.x client members on the root', () => {
  it.each([...MOVED_TO_V0])('reading %s throws guidance naming .v0', (name) => {
    const root = new Daytona({ apiKey: 'key' }) as unknown as Record<string, unknown>
    expect(name in root).toBe(false)
    expect(() => root[name]).toThrow(TypeError)
    expect(() => root[name]).toThrow(`Daytona.v0.${name}`)
  })

  it('names the subclass in the guidance', () => {
    class SandboxPool extends Daytona {}
    const root = new SandboxPool({ apiKey: 'key' }) as unknown as Record<string, unknown>
    expect(() => root.create).toThrow('SDK 2.x moved SandboxPool.create to SandboxPool.v0.create')
  })

  it('names the migration tool release that matches this SDK', () => {
    const root = new Daytona({ apiKey: 'key' }) as unknown as Record<string, unknown>
    expect(() => root.create).toThrow(`To migrate 0.x code, run npx @daytona/upgrade@${SDK_VERSION}.`)
  })

  it('leaves other names, instanceof and inspection alone', () => {
    const root = new Daytona({ apiKey: 'key' })
    expect(root).toBeInstanceOf(Daytona)
    expect((root as unknown as Record<string, unknown>).images).toBeUndefined()
    expect((root as unknown as Record<string, unknown>).then).toBeUndefined()
    expect(() => inspect(root)).not.toThrow()
    expect(root.v2).toBe(root.v2)
  })

  it('matches the upgrade tool mapping', () => {
    const mappingPath = join(__dirname, '..', '..', 'upgrade', 'mapping', 'mapping.json')
    if (!existsSync(mappingPath)) return
    const mapping = JSON.parse(readFileSync(mappingPath, 'utf8')) as { movedClientMembers: { ts: string[] } }
    expect([...mapping.movedClientMembers.ts].sort()).toEqual([...MOVED_TO_V0].sort())
  })
})

describe('root config URL fields (custom API URLs stay on SDK 0.x)', () => {
  it('rejects serverUrl like apiUrl', () => {
    expect(() => new Daytona({ serverUrl: 'https://self.example' } as never)).toThrow('stay on SDK 0.x')
  })

  it('rejects a URL a getter adds while the config is read', () => {
    const config: Record<string, unknown> = {}
    Object.defineProperty(config, 'apiKey', {
      enumerable: true,
      get() {
        config.apiUrl = 'https://self.example/api'
        return 'key'
      },
    })
    expect(() => new Daytona(config as never)).toThrow('stay on SDK 0.x')
  })

  it('does not see later changes to the passed object', () => {
    const config: Record<string, unknown> = { apiKey: 'key' }
    const root = new Daytona(config as never)
    config.apiKey = 'other'
    expect(root.v2['apiKey']).toBe('key')
  })
})

describe('root config snapshot reads prototype getters', () => {
  it('keeps apiKey from a class-instance config', () => {
    class Settings {
      get apiKey(): string {
        return 'class-key'
      }
    }
    const root = new Daytona(new Settings() as never)
    expect(root.v2['apiKey']).toBe('class-key')
  })
})
