// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

import * as fs from 'fs'
import * as path from 'path'
import type { DaytonaConfig, ListSandboxesQuery, Resources, VolumeMount } from '../index'
import type { DaytonaConfigV1, ListSandboxesQueryV1, ResourcesV1, VolumeMountV1 } from '../v1'

// Type-level: the V1 type aliases are the same types as the root ones (fails to compile otherwise).
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false
const typeAliasesMatch: [
  Same<DaytonaConfigV1, DaytonaConfig>,
  Same<ListSandboxesQueryV1, ListSandboxesQuery>,
  Same<ResourcesV1, Resources>,
  Same<VolumeMountV1, VolumeMount>,
] = [true, true, true, true]

const createMock = jest.fn(async () => 'created')
const getMock = jest.fn(async (sandboxId: string) => sandboxId)
const listMock = jest.fn(async function* () {
  yield* []
})
const startMock = jest.fn(async () => undefined)
const stopMock = jest.fn(async () => undefined)
const forkMock = jest.fn(async () => 'forked')
const experimentalForkMock = jest.fn(async () => 'experimental-forked')
const deleteMock = jest.fn(async () => undefined)
const disposeMock = jest.fn(async () => undefined)
const closeMock = jest.fn(async () => undefined)
const createAxiosInstanceMock = jest.fn(() => ({ mocked: true }))
const volumeService = { kind: 'volume-service' }
const snapshotService = { kind: 'snapshot-service' }
const secretService = { kind: 'secret-service' }
const warmPoolService = { kind: 'warm-pool-service' }

jest.mock('../Daytona', () => {
  class MockDaytona {
    public readonly volume = volumeService
    public readonly snapshot = snapshotService
    public readonly secret = secretService
    public readonly warmPool = warmPoolService

    constructor(readonly config?: unknown) {}

    public create = createMock
    public get = getMock
    public list = listMock
    public start = startMock
    public stop = stopMock
    public fork = forkMock
    public _experimental_fork = experimentalForkMock
    public delete = deleteMock
    public [Symbol.asyncDispose] = disposeMock
    public close = closeMock

    public static createAxiosInstance = createAxiosInstanceMock
  }

  return {
    __esModule: true,
    CODE_TOOLBOX_LANGUAGE_LABEL: 'code-toolbox-language',
    CodeLanguage: { PYTHON: 'python', TYPESCRIPT: 'typescript', JAVASCRIPT: 'javascript' },
    Daytona: MockDaytona,
    V1_API_URL: 'https://app.daytona.io/api',
  }
})

const sharedErrors = [
  'DaytonaError',
  'DaytonaBadRequestError',
  'DaytonaAuthenticationError',
  'DaytonaForbiddenError',
  'DaytonaNotFoundError',
  'DaytonaTimeoutError',
  'DaytonaConflictError',
  'DaytonaGoneError',
  'DaytonaUnprocessableEntityError',
  'DaytonaRateLimitError',
  'DaytonaInternalServerError',
  'DaytonaServiceUnavailableError',
  'DaytonaConnectionError',
  'DaytonaValidationError',
  'DaytonaAuthorizationError',
] as const

describe('@daytona/sdk/v1 namespace', () => {
  beforeEach(async () => {
    jest.clearAllMocks()
    const { resetRootNamespaceDeprecationWarningForTests } = await import('../deprecation')
    resetRootNamespaceDeprecationWarningForTests()
  })

  it('exports the implementation class from v1 and preserves alias identity', async () => {
    const root = await import('../index')
    const v1 = await import('../v1')
    const { Daytona: ImplementationDaytona, V1_API_URL } = await import('../Daytona')

    expect(v1.Daytona).toBe(ImplementationDaytona)
    expect(v1.Daytona).not.toBe(root.Daytona)
    expect(v1.DaytonaV1).toBe(ImplementationDaytona)
    expect(v1.ImageV1).toBe(v1.Image)
    expect(v1.SandboxV1).toBe(v1.Sandbox)
    expect(v1.SandboxStateV1).toBe(v1.SandboxState)
    expect(typeAliasesMatch).toEqual([true, true, true, true])
    expect(root.V1_API_URL).toBe(V1_API_URL)
    expect(v1.V1_API_URL).toBe(V1_API_URL)

    expect(v1.DaytonaErrorV1).toBe(v1.DaytonaError)
    expect(v1.DaytonaAuthenticationErrorV1).toBe(v1.DaytonaAuthenticationError)
    expect(v1.DaytonaAuthorizationErrorV1).toBe(v1.DaytonaAuthorizationError)
    expect(v1.DaytonaConflictErrorV1).toBe(v1.DaytonaConflictError)
    expect(v1.DaytonaConnectionErrorV1).toBe(v1.DaytonaConnectionError)
    expect(v1.DaytonaNotFoundErrorV1).toBe(v1.DaytonaNotFoundError)
    expect(v1.DaytonaRateLimitErrorV1).toBe(v1.DaytonaRateLimitError)
    expect(v1.DaytonaTimeoutErrorV1).toBe(v1.DaytonaTimeoutError)
    expect(v1.DaytonaValidationErrorV1).toBe(v1.DaytonaValidationError)
  })

  it('exports the shared error hierarchy from both root and v1', async () => {
    const root = await import('../index')
    const v1 = await import('../v1')

    for (const name of sharedErrors) {
      expect(root).toHaveProperty(name)
      expect(v1).toHaveProperty(name)
      expect(v1[name]).toBe(root[name])
    }
  })

  it('returns the same v1 instance and does not warn when v1 is accessed directly', async () => {
    const emitWarningSpy = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    const { Daytona } = await import('../index')

    const root = new Daytona({ apiKey: 'api-key' })

    expect(root.v1).toBe(root.v1)
    expect(emitWarningSpy).not.toHaveBeenCalled()

    emitWarningSpy.mockRestore()
  })

  it('forwards deprecated root members and warns exactly once until reset', async () => {
    const emitWarningSpy = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    const { Daytona } = await import('../index')
    const { resetRootNamespaceDeprecationWarningForTests } = await import('../deprecation')

    const root = new Daytona({ apiKey: 'api-key' })

    await expect(root.create()).resolves.toBe('created')
    await expect(root.get('sandbox-1')).resolves.toBe('sandbox-1')
    expect(root.volume).toBe(volumeService)
    expect(root.snapshot).toBe(snapshotService)
    expect(root.secret).toBe(secretService)
    expect(root.warmPool).toBe(warmPoolService)

    expect(createMock).toHaveBeenCalledTimes(1)
    expect(getMock).toHaveBeenCalledWith('sandbox-1')
    expect(emitWarningSpy).toHaveBeenCalledTimes(1)
    expect(emitWarningSpy).toHaveBeenCalledWith(
      expect.stringContaining('daytona.v1.create'),
      expect.objectContaining({ code: 'DAYTONA_ROOT_NAMESPACE', type: 'DeprecationWarning' }),
    )

    resetRootNamespaceDeprecationWarningForTests()
    await root.delete('sandbox-1' as never)

    expect(emitWarningSpy).toHaveBeenCalledTimes(2)
    expect(deleteMock).toHaveBeenCalledTimes(1)

    emitWarningSpy.mockRestore()
  })

  it('forwards every deprecated root method with its arguments', async () => {
    const emitWarningSpy = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    const { Daytona } = await import('../index')
    const root = new Daytona({ apiKey: 'api-key' })
    const sandbox = { id: 'sandbox-1' } as never
    const query = { labels: { env: 'dev' } }

    expect(root.list(query)).toBe(listMock.mock.results[0]?.value)
    expect(listMock).toHaveBeenCalledWith(query)
    await root.start(sandbox, 7)
    expect(startMock).toHaveBeenCalledWith(sandbox, 7)
    await root.stop(sandbox)
    expect(stopMock).toHaveBeenCalledWith(sandbox)
    await expect(root.fork(sandbox, { name: 'copy' }, 9)).resolves.toBe('forked')
    expect(forkMock).toHaveBeenCalledWith(sandbox, { name: 'copy' }, 9)
    await expect(root._experimental_fork(sandbox, { name: 'copy' }, 9)).resolves.toBe('experimental-forked')
    expect(experimentalForkMock).toHaveBeenCalledWith(sandbox, { name: 'copy' }, 9)
    await root.delete(sandbox, 30, true)
    expect(deleteMock).toHaveBeenCalledWith(sandbox, 30, true)

    emitWarningSpy.mockRestore()
  })

  it('closes and disposes the v1 client without a deprecation warning', async () => {
    const emitWarningSpy = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    const { Daytona } = await import('../index')

    await new Daytona({ apiKey: 'api-key' }).close()
    expect(closeMock).toHaveBeenCalledTimes(1)
    {
      await using _root = new Daytona({ apiKey: 'api-key' })
    }
    expect(closeMock).toHaveBeenCalledTimes(2)
    await new Daytona({ apiKey: 'api-key' }).v1.close()
    expect(closeMock).toHaveBeenCalledTimes(3)
    expect(emitWarningSpy).not.toHaveBeenCalled()

    emitWarningSpy.mockRestore()
  })

  it('gives the real v1 client a close() that runs its dispose', async () => {
    const { Daytona: DaytonaImpl } = jest.requireActual<typeof import('../Daytona')>('../Daytona')
    const dispose = jest.spyOn(DaytonaImpl.prototype, Symbol.asyncDispose).mockResolvedValue(undefined)

    await DaytonaImpl.prototype.close.call({ [Symbol.asyncDispose]: DaytonaImpl.prototype[Symbol.asyncDispose] })
    expect(dispose).toHaveBeenCalledTimes(1)

    dispose.mockRestore()
  })

  it('forwards static members to the implementation class', async () => {
    const emitWarningSpy = jest.spyOn(process, 'emitWarning').mockImplementation(() => undefined)
    const { Daytona } = await import('../index')

    expect(Daytona.createAxiosInstance(5000)).toEqual({ mocked: true })
    expect(createAxiosInstanceMock).toHaveBeenCalledWith(5000)
    expect(emitWarningSpy).toHaveBeenCalledWith(
      expect.stringContaining('@daytona/sdk/v1 Daytona.createAxiosInstance'),
      expect.objectContaining({ code: 'DAYTONA_ROOT_NAMESPACE', type: 'DeprecationWarning' }),
    )

    emitWarningSpy.mockRestore()
  })

  it('keeps root wrapper parity with every public implementation member', async () => {
    const { Daytona: RootDaytona } = await import('../index')
    // The real (unmocked) class: its prototype lists every method and accessor whatever the declaration form.
    const { Daytona: DaytonaImpl } = jest.requireActual<typeof import('../Daytona')>('../Daytona')

    // TypeScript `private` / `protected` members are still on the prototype at runtime; drop them by name.
    const source = fs.readFileSync(path.join(__dirname, '..', 'Daytona.ts'), 'utf8')
    const nonPublic = new Set(
      [...source.matchAll(/^\s*(?:private|protected)\b(?:\s+(?:static|async|readonly|get|set))*\s+(\w+)/gm)].map(
        (match) => match[1],
      ),
    )
    const isPublic = (name: string) => name !== 'constructor' && !nonPublic.has(name)

    const publicInstanceMembers = new Set<PropertyKey>(
      Object.getOwnPropertyNames(DaytonaImpl.prototype).filter(isPublic),
    )
    publicInstanceMembers.add(Symbol.asyncDispose)
    // Instance fields are not on the prototype: collect declared public fields from the source.
    for (const match of source.matchAll(/^\s*public\s+(?:readonly\s+)?(\w+)\s*[?!]?:/gm)) {
      publicInstanceMembers.add(match[1])
    }
    const publicStaticMembers = Object.getOwnPropertyNames(DaytonaImpl)
      .filter((name) => !['length', 'name', 'prototype'].includes(name))
      .filter(isPublic)

    expect(publicInstanceMembers.has('create')).toBe(true)
    expect(publicInstanceMembers.has('volume')).toBe(true)
    expect(publicStaticMembers).toContain('createAxiosInstance')

    const root = new RootDaytona({ apiKey: 'api-key' })
    const missingInstance = [...publicInstanceMembers].filter((member) => !(member in root)).map(String)
    const missingStatic = publicStaticMembers.filter((member) => !(member in RootDaytona))
    expect(missingInstance).toEqual([])
    expect(missingStatic).toEqual([])
  })
})
