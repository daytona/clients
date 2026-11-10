/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { loadV0, type V0ModuleExports } from '#v0-loader'
import { credentialSafeDiagnostics } from './diagnostics.js'
import { SDK_VERSION } from './version.js'
import { DEFAULT_V2_API_URL, Daytona as DaytonaV2 } from './v2/Daytona.js'
import { DaytonaError as DaytonaErrorV2, NotSupportedError } from './v2/errors/DaytonaError.js'
import {
  DaytonaAuthenticationError as RootDaytonaAuthenticationError,
  DaytonaBadRequestError as RootDaytonaBadRequestError,
  DaytonaConflictError as RootDaytonaConflictError,
  DaytonaConnectionError as RootDaytonaConnectionError,
  DaytonaError as RootDaytonaError,
  DaytonaForbiddenError as RootDaytonaForbiddenError,
  DaytonaGoneError as RootDaytonaGoneError,
  DaytonaInternalServerError as RootDaytonaInternalServerError,
  DaytonaNotFoundError as RootDaytonaNotFoundError,
  DaytonaRateLimitError as RootDaytonaRateLimitError,
  DaytonaServiceUnavailableError as RootDaytonaServiceUnavailableError,
  DaytonaTimeoutError as RootDaytonaTimeoutError,
  DaytonaUnprocessableEntityError as RootDaytonaUnprocessableEntityError,
  registerErrorClasses,
} from './errors.js'
import type { DaytonaConfig } from './types.js'
import { V0_API_URL, snapshotConfig, v0ApiUrl, warnTargetDeprecated } from './root-config.js'

export { DEFAULT_V2_API_URL }
export { V0_API_URL, ROOT_CONFIG_ROUTING, TARGET_DEPRECATION, resetTargetDeprecationWarningForTests } from './root-config.js'

type V0ModuleTypes = typeof import('@daytona/sdk/v0')
/** The v0 SDK's own `Daytona` instance type: what `daytona.v0` returns. The
 * bundle exports the class under the private `RootOnlyClientV0`, read only here
 * because SDK 2.x builds a v0 client only through the root. */
export type DaytonaV0Client = InstanceType<V0ModuleTypes['RootOnlyClientV0']>
/** The v2 client: what `daytona.v2` returns. `@daytona/sdk/v2` does not export
 * the class, so annotate a stored v2 client with this type. */
export type DaytonaV2Client = DaytonaV2
type V0DaytonaConstructor = new (config: Record<string, unknown>) => DaytonaV0Client
type V0ErrorConstructor = (abstract new (...args: never[]) => Error) & Function

/** A loaded v0 module's exports. Spelled out here, not imported from the
 * package-private `#v0-loader`, so the published root `.d.ts` never references
 * a specifier consumers cannot resolve. */
type V0Exports = { readonly [name: string]: unknown }

const V0_NAMESPACE_MISSING = 'The v0 namespace is not included in this build of @daytona/sdk'

const LEGACY_URL_WARNED = Symbol.for('@daytona/sdk/legacy-api-url-warned')

function warnLegacyApiUrl(): void {
  const runtime: { readonly process?: {
    readonly env?: Readonly<Record<string, string | undefined>>
    readonly emitWarning?: (message: string, options: { type: string; code: string }) => void
  } } = globalThis
  const name = 'DAYTONA_API_URL'
  const value = runtime.process?.env?.[name]?.trim().replace(/\/+$/, '')
  if (!value || Reflect.get(globalThis, LEGACY_URL_WARNED) === true) return
  const normalized = value.replace(/^([^:]+):\/\/([^/]+)(.*)$/, (_match: string, scheme: string, host: string, path: string) => `${scheme.toLowerCase()}://${host.toLowerCase()}${path}`)
  if (normalized === V0_API_URL || normalized === DEFAULT_V2_API_URL) return
  Reflect.set(globalThis, LEGACY_URL_WARNED, true)
  const message = 'DAYTONA_API_URL is set, but SDK 2.x ignores it and connects to Daytona Cloud ' +
    '(DAYTONA_V2_API_URL selects the v2 endpoint). For a custom API URL, stay on SDK 0.x.'
  // A runtime with a shimmed `process.env` but no emitWarning (a browser
  // bundle) still gets the warning.
  if (typeof runtime.process?.emitWarning === 'function') runtime.process.emitWarning(message, { type: 'UserWarning', code: 'DAYTONA_LEGACY_API_URL' })
  else console.warn(message)
}

// The catalog's NOT_IMPLEMENTED shape (501 → NotSupportedError): this build
// does not implement the v0 namespace. Not a server error.
class V0NamespaceUnavailableError extends NotSupportedError {
  constructor(cause?: unknown) {
    super(V0_NAMESPACE_MISSING, { statusCode: 501, code: 'NOT_IMPLEMENTED', source: 'SDK', cause })
  }
}

/** Any other failure while loading the v0 namespace: not a missing build. */
class V0NamespaceLoadError extends DaytonaErrorV2 {
  constructor(cause: unknown) {
    super(`importing the v0 namespace failed: ${cause instanceof Error ? cause.message : String(cause)}`, { source: 'SDK', cause })
  }
}

/**
 * True only when the v0 entry itself cannot be resolved (a build without the
 * bundle): Node's ERR_MODULE_NOT_FOUND / MODULE_NOT_FOUND naming
 * `@daytona/sdk/v0` or its missing `dist/cjs/v0/` file. A missing dependency inside the bundle, or any other
 * error, is reported as what it is (V0NamespaceLoadError).
 */
function isMissingV0Entry(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false
  const { code, message } = error as { readonly code?: unknown; readonly message?: unknown }
  if ((code !== 'ERR_MODULE_NOT_FOUND' && code !== 'MODULE_NOT_FOUND') || typeof message !== 'string') return false
  // Only the module that is missing counts (the quoted target on the first
  // line), not the importer: Node appends `imported from …` / a require stack
  // that names dist/cjs/v0 files when one of v0's own dependencies is missing.
  const target = /Cannot find (?:module|package) '([^']+)'/.exec(message.split('\n')[0] ?? '')?.[1]
  if (target === undefined) return false
  // The specifier, or the missing file `exports["./v0"]` points at.
  // The loaders require it relatively ('../v0/index.js', '../dist/cjs/v0/index.js').
  return target === '@daytona/sdk/v0' || /(?:^|[\\/])(?:dist[\\/]cjs[\\/])?v0[\\/]index\.js$/.test(target)
}

/** A module export by name, also under a CommonJS-interop `default`. */
function moduleExport(module: V0ModuleExports, name: string): unknown {
  const direct = module[name]
  if (direct !== undefined) return direct
  const fallback = module.default
  return typeof fallback === 'object' && fallback !== null ? Reflect.get(fallback, name) : undefined
}

function isV0DaytonaConstructor(value: unknown): value is V0DaytonaConstructor {
  return typeof value === 'function'
}

function resolveV0Daytona(module: V0ModuleExports): V0DaytonaConstructor | undefined {
  const exported = moduleExport(module, 'RootOnlyClientV0')
  return isV0DaytonaConstructor(exported) ? exported : undefined
}

type V0Registration = readonly [
  root: Parameters<typeof registerErrorClasses>[0],
  names: readonly string[],
]

const V0_REGISTRATIONS: readonly V0Registration[] = [
  [RootDaytonaError, ['DaytonaError']],
  [RootDaytonaBadRequestError, ['DaytonaBadRequestError']],
  [RootDaytonaAuthenticationError, ['DaytonaAuthenticationError']],
  [RootDaytonaForbiddenError, ['DaytonaForbiddenError']],
  [RootDaytonaNotFoundError, ['DaytonaNotFoundError']],
  [RootDaytonaTimeoutError, ['DaytonaTimeoutError']],
  [RootDaytonaConflictError, ['DaytonaConflictError']],
  [RootDaytonaGoneError, ['DaytonaGoneError']],
  [RootDaytonaUnprocessableEntityError, ['DaytonaUnprocessableEntityError']],
  [RootDaytonaRateLimitError, ['DaytonaRateLimitError']],
  [RootDaytonaInternalServerError, ['DaytonaInternalServerError']],
  [RootDaytonaServiceUnavailableError, ['DaytonaServiceUnavailableError']],
  [RootDaytonaConnectionError, ['DaytonaConnectionError']],
]

function isV0ErrorConstructor(value: unknown): value is V0ErrorConstructor {
  return typeof value === 'function'
}

function exportedFunction(module: V0ModuleExports, name: string): V0ErrorConstructor | undefined {
  const exported = moduleExport(module, name)
  return isV0ErrorConstructor(exported) ? exported : undefined
}

function registerV0Errors(module: V0ModuleExports): void {
  for (const [root, names] of V0_REGISTRATIONS) {
    const members = names
      .map((name) => exportedFunction(module, name))
      .filter((member): member is V0ErrorConstructor => member !== undefined)
    if (members.length > 0) registerErrorClasses(root, members)
  }
}

/** Release a v0 client's resources through its `[Symbol.asyncDispose]()`, when present. */
async function disposeV0(client: unknown): Promise<void> {
  // Node defines Symbol.asyncDispose as the registry symbol
  // Symbol.for('nodejs.asyncDispose'); a vm context (jest, some sandboxes)
  // lacks the property but shares the registry, so fall back to it.
  const key = (Symbol as { readonly asyncDispose?: symbol }).asyncDispose ?? Symbol.for('nodejs.asyncDispose')
  if (client === null || typeof client !== 'object') return
  const dispose: unknown = Reflect.get(client, key)
  if (typeof dispose === 'function') await (dispose as () => Promise<void>).call(client)
}

export class Daytona {
  private readonly config: DaytonaConfig
  private loadV0Module: () => V0ModuleExports = loadV0
  private v2Client?: DaytonaV2Client
  private v0Client?: DaytonaV0Client

  constructor(config?: DaytonaConfig) {
    warnLegacyApiUrl()
    this.config = snapshotConfig(config ?? {})
    credentialSafeDiagnostics(this.config, () => ({ type: 'DaytonaConfig' }))
    credentialSafeDiagnostics(this, () => ({ type: 'Daytona' }))
    if (this.config.target !== undefined) warnTargetDeprecated()
    // Wrapped in the moved-member guard (MOVED_TO_V0_GUARD below).
    return new Proxy(this, MOVED_TO_V0_GUARD)
  }

  public get v2(): DaytonaV2Client {
    this.v2Client ??= new DaytonaV2({
      apiKey: this.config.apiKey,
      jwtToken: this.config.jwtToken,
      projectId: this.config.projectId,
      requestTimeoutSeconds: this.config.requestTimeoutSeconds,
      maxResponseBytes: this.config.maxResponseBytes,
      otelEnabled: this.config.otelEnabled,
      otelApi: this.config.otelApi,
      connectionPoolMaxsize: this.config.connectionPoolMaxsize,
      sharedHub: this.config.sharedHub,
      events: this.config.events,
    })
    return this.v2Client
  }

  /**
   * The v0 namespace: the v0 SDK's own `Daytona` client (the 0.x SDK bundled at
   * a pinned release), the same instance on every access. Built on first access,
   * which loads the v0 SDK synchronously in Node (about 100 ms, once per
   * process); Node code that only uses `v2` never loads it. Browser builds
   * bundle the v0 SDK statically into apps that import the root and evaluate
   * it when the root loads, so `daytona.v0` works there too. A failed
   * load is not cached: the next access retries.
   */
  public get v0(): DaytonaV0Client {
    this.v0Client ??= this.createV0Client()
    return this.v0Client
  }

  /**
   * Close the namespace clients built so far: the v2 client (its sandbox-state
   * events socket) and the v0 client (its event socket and, with `otelEnabled`,
   * its OpenTelemetry SDK, through v0's `[Symbol.asyncDispose]()`). Optional for
   * short scripts — neither keeps a Node process alive — but long-running
   * processes should call it. Idempotent; a later `root.v0` / `root.v2` builds
   * a fresh client. Both are closed even if one fails; the first error is
   * re-thrown (Python parity).
   * Stored v2 clients and their handles throw `ClientClosedError` on new requests.
   */
  public async close(): Promise<void> {
    // Drop the cached clients first (like Python's root close()), so a later
    // access builds a fresh client instead of reusing a closed one (a closed
    // v2 client would silently long-poll every wait).
    const v2 = this.v2Client
    const v0 = this.v0Client
    this.v2Client = undefined
    this.v0Client = undefined
    let failed = false
    let firstError: unknown
    try {
      await v2?.close()
    } catch (error: unknown) {
      failed = true
      firstError = error
    }
    try {
      await disposeV0(v0)
    } catch (error: unknown) {
      if (!failed) {
        failed = true
        firstError = error
      }
    }
    if (failed) throw firstError
  }

  /** `await using daytona = new Daytona()` calls `close()` at the end of the scope. */
  public async [Symbol.asyncDispose](): Promise<void> {
    await this.close()
  }

  private createV0Client(): DaytonaV0Client {
    let V0Daytona: V0DaytonaConstructor | undefined
    try {
      const module = this.loadV0Module()
      if (moduleExport(module, '__daytonaV0Unbundled') === true) throw new V0NamespaceUnavailableError()
      registerV0Errors(module)
      V0Daytona = resolveV0Daytona(module)
    } catch (error: unknown) {
      if (error instanceof V0NamespaceUnavailableError) throw error
      throw isMissingV0Entry(error) ? new V0NamespaceUnavailableError(error) : new V0NamespaceLoadError(error)
    }
    if (V0Daytona === undefined) throw new V0NamespaceUnavailableError(new Error('missing RootOnlyClientV0 export'))
    // v0's own configuration errors (a missing key, …) surface unchanged.
    return new V0Daytona({
      apiKey: this.config.apiKey,
      jwtToken: this.config.jwtToken,
      organizationId: this.config.organizationId,
      target: this.config.target,
      apiUrl: v0ApiUrl(),
      // Python parity (`otel_enabled` reaches v0 too). v0 at v0.230.0 has no
      // pool-size field and takes its timeout in its own unit, so
      // connectionPoolMaxsize / requestTimeoutSeconds stay v2-only (ROOT_CONFIG_ROUTING).
      ...(this.config.otelEnabled === undefined ? {} : { otelEnabled: this.config.otelEnabled }),
    })
  }
}

/**
 * Test seam: make `root` load the v0 namespace through `loader` instead of the
 * synchronous `#v0-loader`. Not exported from the package entry points.
 * @internal
 */
export function setV0ModuleLoaderForTests(root: Daytona, loader: () => V0Exports): Daytona {
  Reflect.set(root, 'loadV0Module', loader)
  return root
}

/**
 * The 0.x client's members, which SDK 2.x moved to `.v0` (the 0.x `Daytona` at
 * the pinned tag; upgrade/mapping/mapping.json `movedClientMembers.ts`
 * is the same list, and tests/root-client.test.ts pins both).
 */
export const MOVED_TO_V0 = ['create', 'get', 'list', 'start', 'stop', 'fork', '_experimental_fork', 'delete', 'volume', 'snapshot', 'secret', 'warmPool'] as const

export function movedToV0Message(name: string, client = 'Daytona'): string {
  // The migration tool is published with every SDK release under the same
  // version, so the guidance names the release that matches this SDK (a
  // source build names its placeholder version).
  return (
    `SDK 2.x moved ${client}.${name} to ${client}.v0.${name} (the Daytona v0 platform; ` +
    `use ${client}.v2 for Daytona v2). To migrate 0.x code, run npx @daytona/upgrade@${SDK_VERSION}.`
  )
}

const MOVED_TO_V0_NAMES: ReadonlySet<string> = new Set(MOVED_TO_V0)

// Runtime guidance for 0.x code that calls a client method on the root. The
// constructor returns the instance wrapped in this proxy: reading a moved name throws, while `'create' in root` stays false
// like Python's `hasattr` (the declarations do not list the names either, so
// TypeScript callers keep their compile error).
const MOVED_TO_V0_GUARD: ProxyHandler<Daytona> = {
  get(target, property, receiver) {
    if (typeof property === 'string' && MOVED_TO_V0_NAMES.has(property) && !Reflect.has(target, property)) {
      const className = target.constructor.name
      const client = className.length >= 3 ? className : 'Daytona'
      throw new TypeError(movedToV0Message(property, client))
    }
    return Reflect.get(target, property, receiver)
  },
}
