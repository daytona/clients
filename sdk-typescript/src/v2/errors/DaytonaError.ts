/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosError } from 'axios'
import { CODE_STATUS } from '../codes.js'
import { BodyBuffer, declaredLength } from '../bodyBuffer.js'
import { JSON_MAX_DEPTH, nestedDeeperThan } from '../jsonDepth.js'
import { enumerableSymbolKey } from '../symbolKeys.js'
import type { KeyedObject } from '../symbolKeys.js'
import type { ApiErrorCode, DaemonErrorCode, ErrorCode, GenericErrorCode, ProxyErrorCode, RouterErrorCode } from '../codes.js'
import type { DockerStatus, SandboxState } from '../types.js'
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
} from '../../errors.js'

/**
 * The wire error envelope, as parsed from a response body. Internal: the
 * public constructors take {@link ErrorOptions}; {@link errorOptionsFromEnvelope}
 * maps one to the other and drops fields of the wrong type.
 */
export interface ErrorEnvelope {
  readonly message?: unknown
  readonly error?: unknown
  readonly source?: unknown
  readonly code?: unknown
  readonly trace_id?: unknown
  readonly retry_after_seconds?: unknown
  readonly feature?: unknown
  readonly entitlement?: unknown
  readonly replacement?: unknown
  readonly firstAvailableCursor?: unknown
  readonly stated?: unknown
  readonly actual?: unknown
  readonly region?: unknown
  readonly limit?: unknown
  readonly current?: unknown
  readonly max?: unknown
  readonly max_bytes?: unknown
  readonly bytes?: unknown
  readonly requestId?: unknown
  readonly retryAfterMs?: unknown
  readonly statusCode?: unknown
  readonly timestamp?: unknown
  readonly path?: unknown
  readonly method?: unknown
}

/**
 * What every error constructor accepts besides its message. Every field is
 * optional; an error built by the caller carries only what it is given.
 * `code` and `source` are open vocabularies (see {@link DaytonaError}).
 */
export interface ErrorOptions {
  readonly statusCode?: number
  readonly code?: ErrorCode | (string & {})
  readonly source?: string
  readonly traceId?: string
  /** `OVERLOADED` / `RATE_LIMITED`: how long to wait before retrying. */
  readonly retryAfterSeconds?: number
  /** `FEATURE_NOT_ENABLED`: the feature the organization lacks. */
  readonly feature?: string
  /** `ENTITLEMENT_REQUIRED`: the entitlement the organization lacks. */
  readonly entitlement?: string
  /** `GONE`: what replaces the removed resource or route. */
  readonly replacement?: string
  /** `CURSOR_EXPIRED`: the oldest cursor still available. */
  readonly firstAvailableCursor?: string
  /** `REGION_MISMATCH`: the region the request stated. */
  readonly stated?: string
  /** `REGION_MISMATCH`: the region actually in effect. */
  readonly actual?: string
  /** `UNKNOWN_REGION`: the unrecognized region. */
  readonly region?: string
  /** `LIMIT_EXCEEDED`: machine name of the exhausted organization limit. */
  readonly limit?: string
  /** `LIMIT_EXCEEDED`: the organization's current count for that limit. */
  readonly current?: number
  /** `LIMIT_EXCEEDED`, `IDLE_OUT_OF_RANGE`, `CREATE_TTL_OUT_OF_RANGE`: the maximum. */
  readonly max?: number
  /** `IMAGE_TOO_LARGE` / `SNAPSHOT_TOO_LARGE`: the size limit in bytes. */
  readonly maxBytes?: number
  /** `IMAGE_TOO_LARGE` / `SNAPSHOT_TOO_LARGE`: the artifact's size in bytes. */
  readonly bytes?: number
  readonly requestId?: string
  readonly retryAfterMs?: number
  /** The underlying failure, when there is one. */
  readonly cause?: unknown
}

/**
 * How a value that is not an options object reads in an error message, or
 * `undefined` when it is one: an object literal, a class instance or a
 * prototype-based object. `null`, primitives, functions, arrays and other
 * built-ins (`Date`, `Map`, ...: their contents are not enumerable keys) are
 * not. Internal: `assertOptionsObject` and the error constructors share it.
 */
export function nonOptionsDescription(value: unknown): string | undefined {
  if (value === null) return 'null'
  if (Array.isArray(value)) return 'an array'
  if (typeof value !== 'object') return `a ${typeof value}`
  const tag = Object.prototype.toString.call(value).slice(8, -1)
  return tag === 'Object' ? undefined : `a built-in ${tag}`
}

/**
 * A plain copy of `options`, refused with `BadRequestError` when it is not an
 * options object: `new NotFoundError('x', 404)` (a positional status from
 * plain JavaScript) would otherwise lose the status silently. The copy holds
 * every enumerable key, own or inherited (`for...in`), as the client
 * configuration reads them, so a prototype-based options object keeps its
 * fields through the spreads the constructors do; an enumerable symbol key
 * is refused. Internal: every error constructor that reads options runs it
 * first.
 */
export function errorOptions<T extends object>(options: T | undefined): T | undefined {
  if (options === undefined) return undefined
  const given = nonOptionsDescription(options)
  if (given !== undefined) {
    throw new BadRequestError(`error options must be an options object, got ${given}`, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  // No error options type declares a symbol key: an enumerable one, own or
  // inherited, is refused, as the options guard (`enumerableKeys`) does. The
  // walk stops before Object.prototype: every options literal the SDK builds
  // itself inherits from it (this function's own BadRequestError included),
  // so a key there would turn every error into this one, and a string key
  // there is not refused either (it is copied, as `for...in` reads it).
  // SAFETY: nonOptionsDescription has established an options object (an
  // object literal, a class instance or a prototype-based object).
  const symbol = enumerableSymbolKey(options as unknown as KeyedObject, Object.prototype as KeyedObject)
  if (symbol !== undefined) {
    throw new BadRequestError(`ErrorOptions has no option \`${String(symbol)}\``, { statusCode: 400, code: 'BAD_REQUEST', source: 'SDK' })
  }
  const copy: Record<string, unknown> = {}
  for (const key in options) copy[key] = (options as Record<string, unknown>)[key]
  return copy as T
}

/**
 * Base class of every v2 error.
 *
 * `code` and `source` are open vocabularies: the server may send a code or
 * source this SDK release does not know yet. An unknown code keeps its exact
 * value on `code` and the error falls back to the class for its HTTP status
 * (`NotFoundError` for a 404, ...). `codes` (from `@daytona/sdk/v2`) lists the
 * codes this release knows.
 */
export class DaytonaError extends Error {
  public readonly statusCode?: number
  public readonly code?: ErrorCode | (string & {})
  public readonly source?: string
  public readonly traceId?: string
  public readonly retryAfterSeconds?: number
  public readonly feature?: string
  public readonly entitlement?: string
  public readonly replacement?: string
  public readonly firstAvailableCursor?: string
  public readonly stated?: string
  public readonly actual?: string
  public readonly region?: string
  public readonly limit?: string
  public readonly current?: number
  public readonly max?: number
  public readonly maxBytes?: number
  public readonly bytes?: number
  public readonly requestId?: string
  public readonly retryAfterMs?: number
  public readonly cause?: unknown

  constructor(message: string, options?: ErrorOptions) {
    super(message)
    this.name = new.target.name
    options = errorOptions(options) ?? {}
    this.statusCode = options.statusCode
    this.code = options.code
    this.source = options.source
    this.traceId = options.traceId
    this.retryAfterSeconds = options.retryAfterSeconds
    this.feature = options.feature
    this.entitlement = options.entitlement
    this.replacement = options.replacement
    this.firstAvailableCursor = options.firstAvailableCursor
    this.stated = options.stated
    this.actual = options.actual
    this.region = options.region
    this.limit = options.limit
    this.current = options.current
    this.max = options.max
    this.maxBytes = options.maxBytes
    this.bytes = options.bytes
    this.requestId = options.requestId
    this.retryAfterMs = options.retryAfterMs
    if (options.cause !== undefined) this.cause = options.cause
    Object.setPrototypeOf(this, new.target.prototype)
  }

  /**
   * Native prototype check, or a match against the same class of another
   * loaded copy of this module (ESM and CJS builds of `@daytona/sdk/v2` in one
   * process): see {@link registerV2ErrorClass}. User subclasses have no key and
   * keep the native check only.
   */
  static [Symbol.hasInstance](value: unknown): boolean {
    if (Function.prototype[Symbol.hasInstance].call(this, value) as boolean) return true
    const key = v2ErrorKeys.get(this)
    if (key === undefined) return false
    return (v2ErrorRegistry().get(key) ?? []).some(
      (member) => member !== this && (Function.prototype[Symbol.hasInstance].call(member, value) as boolean),
    )
  }
}

/**
 * Cross-copy identity of the v2 error classes. Each loaded copy registers its
 * classes under a stable key (an explicit string, never `class.name`, which
 * bundlers may mangle) in a registry on `globalThis` shared by every copy, so
 * `instanceof NotFoundError` from the ESM build matches an error thrown by the
 * CJS build and vice versa. Mirrors the root hierarchy's registry
 * (src/errors.ts).
 */
const V2_ERROR_REGISTRY_KEY = Symbol.for('@daytona/sdk/v2-error-classes')
const v2ErrorKeys = new WeakMap<Function, string>()

function v2ErrorRegistry(): Map<string, Function[]> {
  const holder = globalThis as unknown as Record<symbol, Map<string, Function[]> | undefined>
  let registry = holder[V2_ERROR_REGISTRY_KEY]
  if (registry === undefined) {
    registry = new Map()
    holder[V2_ERROR_REGISTRY_KEY] = registry
  }
  return registry
}

/** Register a v2 error class under its stable cross-copy key. Internal. */
export function registerV2ErrorClass(key: string, errorClass: abstract new (...args: never[]) => DaytonaError): void {
  v2ErrorKeys.set(errorClass, key)
  const registry = v2ErrorRegistry()
  const members = registry.get(key) ?? []
  if (!members.includes(errorClass)) registry.set(key, [...members, errorClass])
}

export class BadRequestError extends DaytonaError {}
export class AuthenticationError extends DaytonaError {}
export class ForbiddenError extends DaytonaError {}
export class NotFoundError extends DaytonaError {}
export class RequestTimeoutError extends DaytonaError {}

export class ConflictError extends DaytonaError {}

/**
 * `sandbox.docker.waitUntilReady()` ran out of caller budget before Docker
 * Engine became `ready`. `status` is the last observed Docker status
 * (`starting`, or `deferred` while the boot has not started Docker yet), or
 * undefined when no status answer arrived before the deadline (for example a
 * stalled first request).
 */
export class DockerWaitTimeoutError extends RequestTimeoutError {
  public readonly status?: DockerStatus

  constructor(status?: DockerStatus) {
    super(`docker did not become ready within the requested timeout${status ? ` (last state: ${status.rawState || status.state})` : ''}`, { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' })
    this.status = status
  }
}

/** Options of `SandboxTerminalStateError` beyond its sandbox id and state. */
export interface SandboxTerminalStateErrorOptions {
  /** The state string the server sent, when it differs from the public `state`. */
  readonly rawState?: string
  /** The server's explanation of the state (for example why a deletion did not finish). */
  readonly reason?: string
}

/**
 * A lifecycle wait (`wait()`, `start()`, `stop()`, ...) saw the sandbox reach a terminal state first.
 * `state` is the observed public sandbox state (e.g. `'destroyed'`).
 */
export class SandboxTerminalStateError extends ConflictError {
  public readonly state: SandboxState
  public readonly rawState?: string
  /**
   * The server's explanation of `state`, when it gave one (for example why a
   * deletion did not finish); the message ends with it.
   */
  public readonly reason?: string

  constructor(sandboxId: string, state: SandboxState, options?: SandboxTerminalStateErrorOptions) {
    const { rawState, reason } = errorOptions(options) ?? {}
    super(`sandbox ${sandboxId} reached terminal state ${state}${reason ? `: ${reason}` : ''}`, { statusCode: 409, code: 'CONFLICT', source: 'SDK' })
    this.state = state
    this.rawState = rawState
    this.reason = reason || undefined
  }
}

/**
 * `sandbox.docker.waitUntilReady()` saw Docker Engine settle in a state that
 * will not become `ready` in this boot (`failed`, `not_present`, `disabled`,
 * `unmanaged`, `unsupported`). `status` is the observed Docker status; its
 * `reason` says why, and `processId` (when set) names the process whose logs
 * hold Docker's output. The sandbox itself stays usable.
 */
export class DockerNotReadyError extends ConflictError {
  public readonly status: DockerStatus

  constructor(status: DockerStatus) {
    super(`docker is ${status.rawState || status.state}${status.reason ? `: ${status.reason}` : ''}`, { statusCode: 409, code: 'CONFLICT', source: 'SDK' })
    this.status = status
  }
}

export class PreconditionFailedError extends DaytonaError {}
export class SecretAlreadyExistsError extends PreconditionFailedError {}
export class GoneError extends DaytonaError {}
export class MethodNotAllowedError extends DaytonaError {}
export class PayloadTooLargeError extends DaytonaError {}
export class UnsupportedMediaTypeError extends DaytonaError {}
export class UnprocessableEntityError extends DaytonaError {}
export class ServiceUnavailableError extends DaytonaError {}
export class InternalServerError extends DaytonaError {}
/**
 * HTTP 429. `RATE_LIMITED` and `TOO_MANY_REQUESTS` throw this class itself
 * (read `code` to tell them apart); `LIMIT_EXCEEDED` and `OVERLOADED` throw its
 * subclasses.
 */
export class RateLimitError extends DaytonaError {}

/**
 * `LIMIT_EXCEEDED` (429): an organization limit is exhausted.
 * Subclasses {@link RateLimitError}, so `catch`/`instanceof RateLimitError` keeps
 * matching; `limit` is the limit's stable machine name (e.g. `max_concurrent_sandboxes`)
 * and `current`/`max` are the counts the refusal was computed from.
 * Fields are `undefined` when a body omits or malforms them.
 */
export class LimitExceededError extends RateLimitError {
  /** `code` defaults to `LIMIT_EXCEEDED`. */
  constructor(message: string, options?: ErrorOptions) {
    options = errorOptions(options) ?? {}
    super(message, { ...options, code: options.code ?? 'LIMIT_EXCEEDED' })
  }
}

/** `IMAGE_TOO_LARGE` (409): `maxBytes` is the limit, `bytes` the image's size. */
export class ImageTooLargeError extends ConflictError {}
/** `SNAPSHOT_TOO_LARGE` (409): `maxBytes` is the limit, `bytes` the snapshot's size. */
export class SnapshotTooLargeError extends ConflictError {}

export class PreconditionRequiredError extends DaytonaError {}
/**
 * The API or the sandbox could not be reached (no HTTP response). `source`
 * defaults to `SDK`; there is no status or code.
 */
export class ConnectionError extends DaytonaError {
  constructor(message: string, options?: ErrorOptions) {
    options = errorOptions(options) ?? {}
    super(message, { ...options, source: options.source ?? 'SDK' })
  }
}
/**
 * The client gave up waiting for a response: its request timeout or a call's
 * own deadline expired. Not the server's HTTP 408 (`RequestTimeoutError`).
 * Defaults: message `'operation timed out'`, `statusCode` 408, `code`
 * `REQUEST_TIMEOUT`, `source` `SDK` (the same in the Python SDK).
 */
export class ClientTimeoutError extends DaytonaError {
  constructor(message = 'operation timed out', options?: ErrorOptions) {
    options = errorOptions(options) ?? {}
    super(message, { ...options, statusCode: options.statusCode ?? 408, code: options.code ?? 'REQUEST_TIMEOUT', source: options.source ?? 'SDK' })
  }
}
/**
 * A local file transfer was cancelled before completion. Defaults: message
 * `'file transfer cancelled'`, `statusCode` 499, `code` `REQUEST_CANCELLED`,
 * `source` `SDK`.
 */
export class TransferCancelledError extends DaytonaError {
  constructor(message = 'file transfer cancelled', options?: ErrorOptions) {
    options = errorOptions(options) ?? {}
    super(message, { ...options, statusCode: options.statusCode ?? 499, code: options.code ?? 'REQUEST_CANCELLED', source: options.source ?? 'SDK' })
  }
}

/**
 * The `create()` request itself timed out before the server answered. The
 * server may still have created the sandbox. `idempotencyKey` is the key the
 * request was sent with. Retry with that key and identical create options, in
 * the same project and API endpoint, to recover the sandbox (or create
 * it if the first request never landed). Always reuse the original create options.
 */
export class SandboxCreateRequestTimeoutError extends ClientTimeoutError {
  public readonly idempotencyKey: string

  constructor(idempotencyKey: string) {
    super(
      `sandbox create request timed out before the server answered; retry create() with idempotencyKey=${JSON.stringify(idempotencyKey)} to reconcile`,
      { statusCode: 408, code: 'REQUEST_TIMEOUT', source: 'SDK' },
    )
    this.idempotencyKey = idempotencyKey
  }
}

/**
 * The connection failed while the `create()` request was in flight. Same
 * outcome as {@link SandboxCreateRequestTimeoutError}: the sandbox may or may
 * not exist; retry `create()` with the same `idempotencyKey` and create options,
 * in the same project and API endpoint.
 */
export class SandboxCreateConnectionError extends ConnectionError {
  public readonly idempotencyKey: string

  constructor(idempotencyKey: string, detail: string) {
    super(
      `sandbox create request failed before the server answered (${detail}); retry create() with idempotencyKey=${JSON.stringify(idempotencyKey)} to reconcile`,
      { source: 'SDK' },
    )
    this.idempotencyKey = idempotencyKey
  }
}
export class NotSupportedError extends InternalServerError {}

export class ProcessNotFoundError extends NotFoundError {}
export class ProcessStartFailedError extends BadRequestError {}
export class ProcessTerminalError extends ConflictError {}
export class ProcessExecutionTimeoutError extends RequestTimeoutError {}
export class ProcessKillIncompleteError extends RequestTimeoutError {}
export class NameConflictError extends ConflictError {}
export class InvalidCursorError extends BadRequestError {}
export class CursorExpiredError extends ConflictError {}
export class StdinClosedError extends ConflictError {}
export class StdinUnavailableError extends ConflictError {}
export class ProtectedProcessError extends ForbiddenError {}
export class UnsupportedOperationError extends BadRequestError {}
export class SessionNotFoundError extends NotFoundError {}
export class SessionEndedError extends GoneError {}
export class KernelUnavailableError extends ServiceUnavailableError {}
export class LspServerUnavailableError extends ServiceUnavailableError {}
export class LspNotStartedError extends ConflictError {}
export class AddonUnavailableError extends ServiceUnavailableError {}
export class FileNotFoundError extends NotFoundError {}
export class InvalidFilePathError extends BadRequestError {}
export class FileAccessDeniedError extends ForbiddenError {}
export class FileReadFailedError extends InternalServerError {}
export class AtomicUploadUnsupportedError extends ConflictError {}
export class GitAuthFailedError extends AuthenticationError {}
export class GitRepoNotFoundError extends NotFoundError {}
export class GitBranchNotFoundError extends NotFoundError {}
export class GitBranchExistsError extends ConflictError {}
export class DesktopNotReadyError extends ConflictError {}

export class OrgSuspendedError extends DaytonaError {}
/**
 * PROJECT_BUDGET_EXCEEDED (402): the project reached its monthly budget. New
 * sandboxes cannot start in it until the budget is raised or removed, or the
 * next calendar month (UTC) begins.
 */
export class ProjectBudgetExceededError extends DaytonaError {}
export class OverloadedError extends RateLimitError {}
export class NotSupportedForClassError extends ConflictError {}
export class FeatureNotEnabledError extends ForbiddenError {}

export class ImageNotFoundError extends NotFoundError {}
export class ImageNotReadyError extends ConflictError {}
/**
 * `IMAGE_FORMAT_OUTDATED` (409): the image is stored in an older format than
 * the current one (`Image.formatOutdated`) and requires conversion.
 * Convert it with `POST /images/{id}/convert`, or create a Linux sandbox from
 * it, which converts it first.
 */
export class ImageFormatOutdatedError extends ConflictError {}
/** {@link ImageBuildFailedError}'s options: the common ones plus the failed image. */
export interface ImageBuildFailedErrorOptions extends ErrorOptions {
  readonly imageId?: string
  readonly buildError?: string
}

/**
 * `IMAGE_BUILD_FAILED` (409), or the SDK's own refusal when
 * `createFromBuild()`'s image build ends `failed`. `imageId` names the failed
 * image (so its build log can be fetched) when the SDK knows it; `buildError`
 * is the build's recorded error, when the image has one. It is separate from
 * `message`, which is always set.
 */
export class ImageBuildFailedError extends ConflictError {
  public readonly imageId?: string
  public readonly buildError?: string

  constructor(message: string, options?: ImageBuildFailedErrorOptions) {
    const { imageId, buildError, ...rest } = errorOptions(options) ?? {}
    super(message, rest)
    this.imageId = imageId
    this.buildError = buildError
  }
}
export class ImageClassMismatchError extends ConflictError {}
export class ImageNameTakenError extends ConflictError {}
export class ImageNameReservedError extends BadRequestError {}
/** `IDLE_OUT_OF_RANGE` (400): `max` is the server's ceiling. */
export class IdleOutOfRangeError extends BadRequestError {}
/** `CREATE_TTL_OUT_OF_RANGE` (400): `max` is the server's ceiling. */
export class CreateTtlOutOfRangeError extends BadRequestError {}
export class SnapshotNotFoundError extends NotFoundError {}
export class SnapshotNotReadyError extends ConflictError {}
export class SnapshotClassMismatchError extends ConflictError {}

export class BillingDisabledError extends NotSupportedError {}
export class BucketCreateFailedError extends InternalServerError {}
export class BucketDeleteFailedError extends InternalServerError {}
export class BucketNotEmptyError extends ConflictError {}
export class OriginImportOnlyError extends BadRequestError {}
export class ReadOnlyUnsupportedError extends BadRequestError {}
export class UnsupportedVolumeTypeError extends BadRequestError {}
export class BillingUnreachableError extends InternalServerError {}
export class BillingUnavailableError extends ServiceUnavailableError {}
export class BillingNotProvisionedError extends ConflictError {}
export class ChargeNotFoundError extends NotFoundError {}
export class InvoiceNotFoundError extends NotFoundError {}
export class CouponNotFoundError extends NotFoundError {}
export class CouponExpiredError extends ConflictError {}
export class CouponLimitReachedError extends ConflictError {}
export class CouponAlreadyRedeemedError extends ConflictError {}
export class InvoiceNotFinalizedError extends ConflictError {}
export class InvoiceNotPayableError extends ConflictError {}
export class ChargeNotSucceededError extends ConflictError {}
export class PlanUnchangedError extends ConflictError {}
export class PlanChangePendingError extends ConflictError {}
export class PlanManagedByOperatorError extends ConflictError {}
export class PaymentMethodRequiredError extends ConflictError {}
export class BillingAccountConflictError extends ConflictError {}
export class UnknownPlanError extends BadRequestError {}
export class PlanRequiresSalesError extends BadRequestError {}
export class AutomaticTopUpNotAvailableError extends ConflictError {}
export class SearchNotConfiguredError extends NotSupportedError {}
export class SearchUnavailableError extends ServiceUnavailableError {}
export class SandboxNotRunningError extends ConflictError {}

export const DAEMON_ERROR_CLASS: Record<DaemonErrorCode, typeof DaytonaError> = {
  PROCESS_NOT_FOUND: ProcessNotFoundError,
  PROCESS_START_FAILED: ProcessStartFailedError,
  PROCESS_TERMINAL: ProcessTerminalError,
  PROCESS_EXECUTION_TIMEOUT: ProcessExecutionTimeoutError,
  PROCESS_KILL_INCOMPLETE: ProcessKillIncompleteError,
  NAME_CONFLICT: NameConflictError,
  INVALID_CURSOR: InvalidCursorError,
  CURSOR_EXPIRED: CursorExpiredError,
  STDIN_CLOSED: StdinClosedError,
  STDIN_UNAVAILABLE: StdinUnavailableError,
  PROTECTED_PROCESS: ProtectedProcessError,
  UNSUPPORTED_OPERATION: UnsupportedOperationError,
  SESSION_NOT_FOUND: SessionNotFoundError,
  SESSION_ENDED: SessionEndedError,
  KERNEL_UNAVAILABLE: KernelUnavailableError,
  LSP_SERVER_UNAVAILABLE: LspServerUnavailableError,
  LSP_NOT_STARTED: LspNotStartedError,
  ADDON_UNAVAILABLE: AddonUnavailableError,
  FILE_NOT_FOUND: FileNotFoundError,
  INVALID_FILE_PATH: InvalidFilePathError,
  FILE_ACCESS_DENIED: FileAccessDeniedError,
  FILE_READ_FAILED: FileReadFailedError,
  ATOMIC_UPLOAD_UNSUPPORTED: AtomicUploadUnsupportedError,
  GIT_AUTH_FAILED: GitAuthFailedError,
  GIT_REPO_NOT_FOUND: GitRepoNotFoundError,
  GIT_BRANCH_NOT_FOUND: GitBranchNotFoundError,
  GIT_BRANCH_EXISTS: GitBranchExistsError,
  DESKTOP_NOT_READY: DesktopNotReadyError,
}

export const API_ERROR_CLASS: Record<ApiErrorCode, typeof DaytonaError> = {
  BILLING_DISABLED: BillingDisabledError,
  BILLING_UNREACHABLE: BillingUnreachableError,
  BILLING_UNAVAILABLE: BillingUnavailableError,
  BILLING_NOT_PROVISIONED: BillingNotProvisionedError,
  CHARGE_NOT_FOUND: ChargeNotFoundError,
  INVOICE_NOT_FOUND: InvoiceNotFoundError,
  COUPON_NOT_FOUND: CouponNotFoundError,
  COUPON_EXPIRED: CouponExpiredError,
  COUPON_LIMIT_REACHED: CouponLimitReachedError,
  COUPON_ALREADY_REDEEMED: CouponAlreadyRedeemedError,
  INVOICE_NOT_FINALIZED: InvoiceNotFinalizedError,
  INVOICE_NOT_PAYABLE: InvoiceNotPayableError,
  CHARGE_NOT_SUCCEEDED: ChargeNotSucceededError,
  PLAN_UNCHANGED: PlanUnchangedError,
  PLAN_CHANGE_PENDING: PlanChangePendingError,
  PLAN_MANAGED_BY_OPERATOR: PlanManagedByOperatorError,
  PAYMENT_METHOD_REQUIRED: PaymentMethodRequiredError,
  BILLING_ACCOUNT_CONFLICT: BillingAccountConflictError,
  UNKNOWN_PLAN: UnknownPlanError,
  PLAN_REQUIRES_SALES: PlanRequiresSalesError,
  AUTOMATIC_TOP_UP_NOT_AVAILABLE: AutomaticTopUpNotAvailableError,
  BUCKET_CREATE_FAILED: BucketCreateFailedError,
  BUCKET_DELETE_FAILED: BucketDeleteFailedError,
  BUCKET_NOT_EMPTY: BucketNotEmptyError,
  ORIGIN_IMPORT_ONLY: OriginImportOnlyError,
  READ_ONLY_UNSUPPORTED: ReadOnlyUnsupportedError,
  UNSUPPORTED_VOLUME_TYPE: UnsupportedVolumeTypeError,
  CREATE_BODY_TOO_LARGE: PayloadTooLargeError,
  INVALID_PARENT_REGION: BadRequestError,
  INVALID_PREVIEW_DOMAIN: BadRequestError,
  INVALID_SANDBOX_CIDR: BadRequestError,
  INVALID_TOOLBOX_PROXY_URL: BadRequestError,
  REGION_LIMIT_REACHED: ConflictError,
  REGION_NOT_EMPTY: ConflictError,
  REGION_NOT_FOUND: NotFoundError,
  REGION_SUBNETS_EXHAUSTED: ConflictError,
  REGION_UNAVAILABLE: ConflictError,
  RUNNER_HAS_SANDBOXES: ConflictError,
  DISK_NOT_READY: ConflictError,
  ROLE_IN_USE: ConflictError,
  ECR_ACCOUNT_MISMATCH: BadRequestError,
  ECR_CREDENTIALS_INVALID: BadRequestError,
  ECR_OWNERSHIP_UNVERIFIABLE: InternalServerError,
  EMAIL_ACCESS_DENIED: ForbiddenError,
  ENTITLEMENT_REQUIRED: ForbiddenError,
  FEATURE_NOT_ENABLED: FeatureNotEnabledError,
  INVALID_FOR_SOURCE: BadRequestError,
  LIMIT_EXCEEDED: LimitExceededError,
  USAGE_UNAVAILABLE: ServiceUnavailableError,
  BAD_RANGE: BadRequestError,
  BAD_RESOLUTION: BadRequestError,
  BILLING_FORBIDDEN: InternalServerError,
  BILLING_ERROR: InternalServerError,
  IF_MATCH_REQUIRED: PreconditionRequiredError,
  SECRETS_TRANSITION_IN_PROGRESS: ConflictError,
  STALE_SECRETS_REVISION: PreconditionFailedError,
  MANAGEMENT_PLANE_ELSEWHERE: NotSupportedError,
  NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL: BadRequestError,
  NETWORK_POLICY_TRANSITION_IN_PROGRESS: ConflictError,
  STALE_NETWORK_POLICY_REVISION: PreconditionFailedError,
  NOT_SUPPORTED_FOR_CLASS: NotSupportedForClassError,
  IMAGE_NOT_FOUND: ImageNotFoundError,
  IMAGE_NOT_READY: ImageNotReadyError,
  IMAGE_BUILD_FAILED: ImageBuildFailedError,
  DEFAULT_IMAGE_NOT_CONFIGURED: BadRequestError,
  IMAGE_FORMAT_OUTDATED: ImageFormatOutdatedError,
  IMAGE_CLASS_MISMATCH: ImageClassMismatchError,
  IMAGE_NAME_TAKEN: ImageNameTakenError,
  IMAGE_NAME_RESERVED: ImageNameReservedError,
  IMAGE_TOO_LARGE: ImageTooLargeError,
  IDLE_OUT_OF_RANGE: IdleOutOfRangeError,
  CREATE_TTL_OUT_OF_RANGE: CreateTtlOutOfRangeError,
  SNAPSHOT_NOT_FOUND: SnapshotNotFoundError,
  SNAPSHOT_NOT_READY: SnapshotNotReadyError,
  SNAPSHOT_CLASS_MISMATCH: SnapshotClassMismatchError,
  SNAPSHOT_IN_PROGRESS: ConflictError,
  SNAPSHOT_TOO_LARGE: SnapshotTooLargeError,
  IMPORTED_KEY_ALWAYS_SYNCED: ConflictError,
  KEY_SYNC_NOT_APPLICABLE: ConflictError,
  ORG_SUSPENDED: OrgSuspendedError,
  PROJECT_BUDGET_EXCEEDED: ProjectBudgetExceededError,
  OVERLOADED: OverloadedError,
  PLACEMENT_UNAVAILABLE: ServiceUnavailableError,
  RATE_LIMITED: RateLimitError,
  SEARCH_NOT_CONFIGURED: SearchNotConfiguredError,
  SEARCH_UNAVAILABLE: SearchUnavailableError,
  SIGNUP_NOT_INVITED: ForbiddenError,
  USER_SUSPENDED: ForbiddenError,
  SOURCE_AMBIGUOUS: ConflictError,
  SECRET_ALREADY_EXISTS: SecretAlreadyExistsError,
  WORKLOAD_PLANE_ELSEWHERE: NotSupportedError,
  NO_ACTIVE_RESERVATION: ConflictError,
  NO_FREE_SLOT: ConflictError,
  RESERVATION_NOT_FOUND: NotFoundError,
  NO_MAC_AVAILABLE: ConflictError,
  MAC_DISK_FULL: ConflictError,
  AUTO_RESERVE_LIMIT_REACHED: ConflictError,
  IDEMPOTENCY_KEY_REUSED: ConflictError,
  INSUFFICIENT_BALANCE: DaytonaError,
  CHARGE_FAILED: DaytonaError,
  V0_BRIDGE_UNAVAILABLE: ServiceUnavailableError,
  V0_SANDBOX_NOT_FOUND: NotFoundError,
  V0_SANDBOX_NOT_IMPORTABLE: ConflictError,
  V0_BACKUP_REGISTRY_UNCONFIGURED: ServiceUnavailableError,
  V0_MIGRATION_DEFAULT_PROJECT_ONLY: ForbiddenError,
}

export const PROXY_ERROR_CLASS: Record<ProxyErrorCode, typeof DaytonaError> = {
  SANDBOX_NOT_RUNNING: SandboxNotRunningError,
}

export const ROUTER_ERROR_CLASS: Record<RouterErrorCode, typeof DaytonaError> = {
  REGION_MISMATCH: BadRequestError,
  UNKNOWN_REGION: BadRequestError,
  REGION_REQUIRED: BadRequestError,
  IDEMPOTENCY_KEY_REQUIRED: BadRequestError,
  CREATE_BODY_TOO_LARGE: PayloadTooLargeError,
  PLACEMENT_UNAVAILABLE: ServiceUnavailableError,
}

export const GENERIC_ERROR_CLASS: Readonly<Record<string, typeof DaytonaError>> = {
  BAD_GATEWAY: InternalServerError,
  BAD_REQUEST: BadRequestError,
  CONFLICT: ConflictError,
  FORBIDDEN: ForbiddenError,
  GATEWAY_TIMEOUT: InternalServerError,
  GONE: GoneError,
  INTERNAL_SERVER_ERROR: InternalServerError,
  METHOD_NOT_ALLOWED: MethodNotAllowedError,
  NOT_FOUND: NotFoundError,
  NOT_IMPLEMENTED: NotSupportedError,
  PAYMENT_REQUIRED: DaytonaError,
  PAYLOAD_TOO_LARGE: PayloadTooLargeError,
  REQUEST_TIMEOUT: RequestTimeoutError,
  SERVICE_UNAVAILABLE: ServiceUnavailableError,
  TOO_MANY_REQUESTS: RateLimitError,
  UNAUTHORIZED: AuthenticationError,
  UNPROCESSABLE_ENTITY: UnprocessableEntityError,
  UNSUPPORTED_MEDIA_TYPE: UnsupportedMediaTypeError,
} satisfies Record<GenericErrorCode, typeof DaytonaError>

export const CODE_ERROR_CLASS: Readonly<Record<string, typeof DaytonaError>> = {
  ...DAEMON_ERROR_CLASS,
  ...API_ERROR_CLASS,
  ...PROXY_ERROR_CLASS,
  ...ROUTER_ERROR_CLASS,
  ...GENERIC_ERROR_CLASS,
}

const SOURCE_ERROR_CLASS: Readonly<Record<string, Readonly<Record<string, typeof DaytonaError>>>> = {
  DAYTONA_API: API_ERROR_CLASS,
  DAYTONA_DAEMON: DAEMON_ERROR_CLASS,
  DAYTONA_PROXY: PROXY_ERROR_CLASS,
  DAYTONA_ROUTER: ROUTER_ERROR_CLASS,
  // SDK-synthesized errors reuse the toolbox error classes so callers can
  // handle timeouts and terminal states consistently across error sources.
  SDK: DAEMON_ERROR_CLASS,
}

const STATUS_ERROR_CLASS: Record<number, typeof DaytonaError> = {
  400: BadRequestError,
  401: AuthenticationError,
  403: ForbiddenError,
  404: NotFoundError,
  408: RequestTimeoutError,
  409: ConflictError,
  412: PreconditionFailedError,
  410: GoneError,
  405: MethodNotAllowedError,
  413: PayloadTooLargeError,
  415: UnsupportedMediaTypeError,
  422: UnprocessableEntityError,
  428: PreconditionRequiredError,
  429: RateLimitError,
  500: InternalServerError,
  501: NotSupportedError,
  503: ServiceUnavailableError,
}

export function errorClassFromStatusCode(statusCode?: number): typeof DaytonaError {
  if (statusCode === undefined) return DaytonaError
  return STATUS_ERROR_CLASS[statusCode] ?? (statusCode >= 500 ? InternalServerError : DaytonaError)
}

function optionalText(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function optionalInteger(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) ? value : undefined
}

/**
 * The constructor options carried by a wire envelope. Fields of the wrong
 * type are dropped, like the Python SDK's envelope parser does. Internal.
 */
export function errorOptionsFromEnvelope(envelope: ErrorEnvelope | undefined): ErrorOptions {
  if (envelope === undefined) return {}
  const retryAfterMs = optionalInteger(envelope.retryAfterMs)
  return {
    traceId: optionalText(envelope.trace_id),
    retryAfterSeconds: optionalInteger(envelope.retry_after_seconds),
    feature: optionalText(envelope.feature),
    entitlement: optionalText(envelope.entitlement),
    replacement: optionalText(envelope.replacement),
    firstAvailableCursor: optionalText(envelope.firstAvailableCursor),
    stated: optionalText(envelope.stated),
    actual: optionalText(envelope.actual),
    region: optionalText(envelope.region),
    limit: optionalText(envelope.limit),
    current: optionalInteger(envelope.current),
    max: optionalInteger(envelope.max),
    maxBytes: optionalInteger(envelope.max_bytes),
    bytes: optionalInteger(envelope.bytes),
    requestId: optionalText(envelope.requestId),
    retryAfterMs: retryAfterMs !== undefined && retryAfterMs >= 0 ? retryAfterMs : undefined,
  }
}

function ownClass<T extends object>(map: T | undefined, key: string): T[keyof T] | undefined {
  return map && Object.prototype.hasOwnProperty.call(map, key) ? map[key as keyof T] : undefined
}

/**
 * The typed error for a `(source, code)` pair, else for the code alone, else
 * for the status. Internal: callers outside the SDK construct the class they
 * want directly.
 */
export function createDaytonaError(message: string, statusCode?: number, code?: string, source?: string, extra: ErrorOptions = {}): DaytonaError {
  const ErrorClass =
    ownClass(ownClass(SOURCE_ERROR_CLASS, source ?? ''), code ?? '') ??
    ownClass(GENERIC_ERROR_CLASS, code ?? '') ??
    errorClassFromStatusCode(statusCode)
  return new ErrorClass(message, { ...extra, statusCode, code, source })
}

/**
 * The error for a 3xx response (and a browser's opaque redirect, whose status
 * reads 0): the SDK follows no redirect, whatever the body says, as in Python.
 * @internal
 */
export function redirectError(status: number | undefined): DaytonaError {
  const shown = status === undefined || status === 0 ? 'HTTP redirect' : `HTTP ${status}`
  return createDaytonaError(`${shown}: unexpected redirect (the SDK does not follow redirects)`, status || undefined, undefined, 'SDK')
}

/**
 * Whether a status is a redirect the SDK refuses: a 3xx, or the 0 of a fetch
 * response that is opaque (a browser's manual redirect). @internal
 */
export function isRedirectStatus(status: number | undefined): boolean {
  return status === 0 || (status !== undefined && status >= 300 && status < 400)
}

/**
 * Whether a response's media type is exactly `application/json` (type and
 * subtype compared case-insensitively, parameters ignored), as the Python
 * SDK's `is_json_media_type`: `application/jsonl` is not JSON. Only an error
 * body declared this way is read for a code.
 */
export function isJsonMediaType(contentType: unknown): boolean {
  if (typeof contentType !== 'string') return false
  return (contentType.split(';')[0] ?? '').trim().toLowerCase() === 'application/json'
}

/** A header of an axios response (`AxiosHeaders` or a plain record), case-insensitively. */
export function axiosResponseHeader(headers: unknown, name: string): unknown {
  if (headers === null || typeof headers !== 'object') return undefined
  const get = (headers as { get?: unknown }).get
  if (typeof get === 'function') return (get as (this: unknown, header: string) => unknown).call(headers, name)
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(headers)) if (key.toLowerCase() === wanted) return value
  return undefined
}

/** An error body's text for the message when it is not read as an envelope. */
function errorBodyText(data: unknown): string | undefined {
  if (typeof data === 'string') return data
  if (data instanceof ArrayBuffer) return new TextDecoder().decode(data)
  if (ArrayBuffer.isView(data)) return new TextDecoder().decode(new Uint8Array(data.buffer, data.byteOffset, data.byteLength))
  // axios parses a body that looks like JSON whatever its media type.
  if (data !== null && typeof data === 'object') return JSON.stringify(data)
  return undefined
}

function dataObject(data: unknown): ErrorEnvelope | undefined {
  if (data && typeof data === 'object') return data as ErrorEnvelope
  return undefined
}

function messageFromEnvelope(envelope: ErrorEnvelope | undefined, fallback: string): string {
  // `error` is a prose fallback for bodies without `message`, never a code.
  // Error-class dispatch stays on `code`/`source`/status.
  if (typeof envelope?.message === 'string') return envelope.message
  if (typeof envelope?.error === 'string') return envelope.error
  return fallback
}

function isAxiosTimeoutError(error: AxiosError): boolean {
  return error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT' || error.message.includes('timeout of')
}

/**
 * The axios error code the SDK's adapter gives a success response whose body
 * passed the `maxResponseBytes` cap (internal); it becomes `PayloadTooLargeError`.
 */
export const PAYLOAD_TOO_LARGE_AXIOS_CODE = 'ERR_DAYTONA_RESPONSE_TOO_LARGE'

/** A success response whose body passed the `maxResponseBytes` cap. */
export function responseTooLargeError(message: string): PayloadTooLargeError {
  return new PayloadTooLargeError(message, { statusCode: 413, code: 'PAYLOAD_TOO_LARGE', source: 'SDK' })
}

/**
 * An error response whose JSON body nests deeper than `JSON_MAX_DEPTH`: the
 * body is unreadable, as an oversized one is, so the error keeps its status's
 * class and carries no code or details from it (as in the Python SDK).
 */
function tooDeepErrorBody(status: number): DaytonaError {
  return createDaytonaError(
    `HTTP ${status}: the response body was nested too deeply to read (over ${JSON_MAX_DEPTH} levels of JSON)`,
    status,
  )
}

export function createAxiosDaytonaError(error: AxiosError): DaytonaError {
  if (error.code === PAYLOAD_TOO_LARGE_AXIOS_CODE) return responseTooLargeError(error.message)
  // Only a body declared as application/json carries a code.
  const json = isJsonMediaType(axiosResponseHeader(error.response?.headers, 'content-type'))
  const status = error.response?.status
  if (json && status !== undefined && nestedDeeperThan(error.response?.data)) return tooDeepErrorBody(status)
  const envelope = json ? dataObject(error.response?.data) : undefined
  const code = typeof envelope?.code === 'string' ? envelope.code : undefined
  const source = typeof envelope?.source === 'string' ? envelope.source : undefined
  if (isAxiosTimeoutError(error)) return new ClientTimeoutError()
  if (!error.response && (error.request || error.code)) return new ConnectionError(error.message)
  if (isRedirectStatus(status)) return redirectError(status)
  const fallback = typeof error.response?.data === 'string' ? error.response.data : (json ? undefined : errorBodyText(error.response?.data)) || error.message
  return createDaytonaError(messageFromEnvelope(envelope, fallback), status, code, source, errorOptionsFromEnvelope(envelope))
}

// Reads the unified envelope off a string body for callers that read the body
// themselves (build logs, with their own size/deadline bounds), so they
// classify errors the same way createAxiosDaytonaError does. Not an envelope,
// or not JSON, falls back to fallbackMessage + status-level dispatch.
// emptyProseIsAbsent makes an empty-string message/error use the fallback too,
// so the build-log readers agree with the Python SDK on a prose-less body.
export function errorFromEnvelopeBody(
  body: string,
  contentType: string | null | undefined,
  status: number,
  fallbackMessage: string,
  emptyProseIsAbsent = false,
): DaytonaError {
  const parsed = isJsonMediaType(contentType) ? parsedBody(body) : undefined
  // Nested past JSON_MAX_DEPTH: unreadable, as on the axios and fetch paths.
  if (nestedDeeperThan(parsed)) return tooDeepErrorBody(status)
  return errorFromEnvelope(parsed, status, fallbackMessage, emptyProseIsAbsent)
}

function parsedBody(body: string): unknown {
  try {
    return JSON.parse(body) as unknown
  } catch {
    return undefined
  }
}

function errorFromEnvelope(
  body: unknown,
  status: number,
  fallbackMessage: string,
  emptyProseIsAbsent: boolean,
): DaytonaError {
  if (isRedirectStatus(status)) return redirectError(status)
  const parsed = dataObject(body)
  const code = typeof parsed?.code === 'string' ? parsed.code : undefined
  const source = typeof parsed?.source === 'string' ? parsed.source : undefined
  // emptyProseIsAbsent picks the first non-empty of message/error before the
  // fallback — matching Python's `message or error or fallback` so an empty
  // message never masks a non-empty error.
  const message = emptyProseIsAbsent
    ? (typeof parsed?.message === 'string' && parsed.message) || (typeof parsed?.error === 'string' && parsed.error) || fallbackMessage
    : messageFromEnvelope(parsed, fallbackMessage)
  return createDaytonaError(message, status, code, source, errorOptionsFromEnvelope(parsed))
}

/**
 * The typed error of a failed fetch response, its body read under `limit`
 * bytes (`0` = no limit; a declared or actual body over it is not read on:
 * the error keeps its status's class and says the body was too large).
 */
export async function errorFromFetchResponse(response: Response, limit = 0, onChunk?: () => void): Promise<DaytonaError> {
  if (isRedirectStatus(response.status) || response.type === 'opaqueredirect') {
    await response.body?.cancel().catch(() => undefined)
    return redirectError(response.status)
  }
  const text = await readFetchText(response, limit, onChunk)
  if (text === undefined) {
    return createDaytonaError(
      `HTTP ${response.status}: the response body was too large to read (over the ${limit}-byte maxResponseBytes limit)`,
      response.status,
    )
  }
  // Only a body declared as application/json carries a code.
  const parsed = isJsonMediaType(response.headers.get('content-type')) ? parsedBody(text) : undefined
  if (nestedDeeperThan(parsed)) return tooDeepErrorBody(response.status)
  return errorFromEnvelope(parsed, response.status, text || response.statusText, false)
}

/**
 * A fetch response's body as text, read under `limit` bytes (`0` = no
 * limit), or `undefined` once it passes it (the body is then cancelled).
 */
export async function readFetchText(response: Response, limit: number, onChunk?: () => void): Promise<string | undefined> {
  if (limit <= 0 && onChunk === undefined) return response.text()
  const declared = response.headers.get('content-length')?.trim() ?? ''
  if (limit > 0 && /^\d+$/.test(declared) && Number(declared) > limit) {
    await response.body?.cancel().catch(() => undefined)
    return undefined
  }
  const reader = response.body?.getReader()
  if (reader === undefined) return ''
  // Allocated once when the length is declared (see BodyBuffer for the peak).
  const body = new BodyBuffer(limit, declaredLength(declared))
  try {
    for (;;) {
      const next = await reader.read()
      // `onChunk`: progress (a transfer's idle bound restarts on each chunk).
      onChunk?.()
      if (next.done) break
      if (!body.add(next.value)) {
        await reader.cancel().catch(() => undefined)
        return undefined
      }
    }
  } finally {
    reader.releaseLock()
  }
  const bytes = body.bytes()
  // Too large to hold at all: refused like a body over the cap.
  return bytes === undefined ? undefined : new TextDecoder().decode(bytes)
}

export function statusForCode(code: ErrorCode): number {
  return CODE_STATUS[code]
}

registerErrorClasses(RootDaytonaError, [DaytonaError])
registerErrorClasses(RootDaytonaBadRequestError, [BadRequestError])
registerErrorClasses(RootDaytonaAuthenticationError, [AuthenticationError])
registerErrorClasses(RootDaytonaForbiddenError, [ForbiddenError])
registerErrorClasses(RootDaytonaNotFoundError, [NotFoundError])
registerErrorClasses(RootDaytonaTimeoutError, [RequestTimeoutError, ClientTimeoutError])
registerErrorClasses(RootDaytonaConflictError, [ConflictError])
registerErrorClasses(RootDaytonaGoneError, [GoneError])
registerErrorClasses(RootDaytonaUnprocessableEntityError, [UnprocessableEntityError])
registerErrorClasses(RootDaytonaRateLimitError, [RateLimitError])
registerErrorClasses(RootDaytonaInternalServerError, [InternalServerError])
registerErrorClasses(RootDaytonaServiceUnavailableError, [ServiceUnavailableError])
registerErrorClasses(RootDaytonaConnectionError, [ConnectionError])

// Every error class in this module, keyed for cross-copy instanceof (a drift test
// checks the list against the module's exports).
for (const [key, errorClass] of [
  ['DaytonaError', DaytonaError],
  ['BadRequestError', BadRequestError],
  ['AuthenticationError', AuthenticationError],
  ['ForbiddenError', ForbiddenError],
  ['NotFoundError', NotFoundError],
  ['RequestTimeoutError', RequestTimeoutError],
  ['ConflictError', ConflictError],
  ['DockerWaitTimeoutError', DockerWaitTimeoutError],
  ['SandboxTerminalStateError', SandboxTerminalStateError],
  ['DockerNotReadyError', DockerNotReadyError],
  ['PreconditionFailedError', PreconditionFailedError],
  ['SecretAlreadyExistsError', SecretAlreadyExistsError],
  ['GoneError', GoneError],
  ['MethodNotAllowedError', MethodNotAllowedError],
  ['PayloadTooLargeError', PayloadTooLargeError],
  ['UnsupportedMediaTypeError', UnsupportedMediaTypeError],
  ['UnprocessableEntityError', UnprocessableEntityError],
  ['ServiceUnavailableError', ServiceUnavailableError],
  ['InternalServerError', InternalServerError],
  ['RateLimitError', RateLimitError],
  ['LimitExceededError', LimitExceededError],
  ['ImageTooLargeError', ImageTooLargeError],
  ['SnapshotTooLargeError', SnapshotTooLargeError],
  ['PreconditionRequiredError', PreconditionRequiredError],
  ['ConnectionError', ConnectionError],
  ['ClientTimeoutError', ClientTimeoutError],
  ['TransferCancelledError', TransferCancelledError],
  ['SandboxCreateRequestTimeoutError', SandboxCreateRequestTimeoutError],
  ['SandboxCreateConnectionError', SandboxCreateConnectionError],
  ['NotSupportedError', NotSupportedError],
  ['ProcessNotFoundError', ProcessNotFoundError],
  ['ProcessStartFailedError', ProcessStartFailedError],
  ['ProcessTerminalError', ProcessTerminalError],
  ['ProcessExecutionTimeoutError', ProcessExecutionTimeoutError],
  ['ProcessKillIncompleteError', ProcessKillIncompleteError],
  ['NameConflictError', NameConflictError],
  ['InvalidCursorError', InvalidCursorError],
  ['CursorExpiredError', CursorExpiredError],
  ['StdinClosedError', StdinClosedError],
  ['StdinUnavailableError', StdinUnavailableError],
  ['ProtectedProcessError', ProtectedProcessError],
  ['UnsupportedOperationError', UnsupportedOperationError],
  ['SessionNotFoundError', SessionNotFoundError],
  ['SessionEndedError', SessionEndedError],
  ['KernelUnavailableError', KernelUnavailableError],
  ['LspServerUnavailableError', LspServerUnavailableError],
  ['LspNotStartedError', LspNotStartedError],
  ['AddonUnavailableError', AddonUnavailableError],
  ['FileNotFoundError', FileNotFoundError],
  ['InvalidFilePathError', InvalidFilePathError],
  ['FileAccessDeniedError', FileAccessDeniedError],
  ['FileReadFailedError', FileReadFailedError],
  ['AtomicUploadUnsupportedError', AtomicUploadUnsupportedError],
  ['GitAuthFailedError', GitAuthFailedError],
  ['GitRepoNotFoundError', GitRepoNotFoundError],
  ['GitBranchNotFoundError', GitBranchNotFoundError],
  ['GitBranchExistsError', GitBranchExistsError],
  ['DesktopNotReadyError', DesktopNotReadyError],
  ['OrgSuspendedError', OrgSuspendedError],
  ['ProjectBudgetExceededError', ProjectBudgetExceededError],
  ['OverloadedError', OverloadedError],
  ['NotSupportedForClassError', NotSupportedForClassError],
  ['ImageNotFoundError', ImageNotFoundError],
  ['ImageNotReadyError', ImageNotReadyError],
  ['ImageFormatOutdatedError', ImageFormatOutdatedError],
  ['ImageBuildFailedError', ImageBuildFailedError],
  ['ImageClassMismatchError', ImageClassMismatchError],
  ['ImageNameTakenError', ImageNameTakenError],
  ['ImageNameReservedError', ImageNameReservedError],
  ['IdleOutOfRangeError', IdleOutOfRangeError],
  ['CreateTtlOutOfRangeError', CreateTtlOutOfRangeError],
  ['SnapshotNotFoundError', SnapshotNotFoundError],
  ['SnapshotNotReadyError', SnapshotNotReadyError],
  ['SnapshotClassMismatchError', SnapshotClassMismatchError],
  ['BillingDisabledError', BillingDisabledError],
  ['BucketCreateFailedError', BucketCreateFailedError],
  ['BucketDeleteFailedError', BucketDeleteFailedError],
  ['BucketNotEmptyError', BucketNotEmptyError],
  ['OriginImportOnlyError', OriginImportOnlyError],
  ['ReadOnlyUnsupportedError', ReadOnlyUnsupportedError],
  ['UnsupportedVolumeTypeError', UnsupportedVolumeTypeError],
  ['BillingUnreachableError', BillingUnreachableError],
  ['SearchNotConfiguredError', SearchNotConfiguredError],
  ['SearchUnavailableError', SearchUnavailableError],
  ['SandboxNotRunningError', SandboxNotRunningError],
  ['FeatureNotEnabledError', FeatureNotEnabledError],
  ['BillingUnavailableError', BillingUnavailableError],
  ['BillingNotProvisionedError', BillingNotProvisionedError],
  ['ChargeNotFoundError', ChargeNotFoundError],
  ['InvoiceNotFoundError', InvoiceNotFoundError],
  ['CouponNotFoundError', CouponNotFoundError],
  ['CouponExpiredError', CouponExpiredError],
  ['CouponLimitReachedError', CouponLimitReachedError],
  ['CouponAlreadyRedeemedError', CouponAlreadyRedeemedError],
  ['InvoiceNotFinalizedError', InvoiceNotFinalizedError],
  ['InvoiceNotPayableError', InvoiceNotPayableError],
  ['ChargeNotSucceededError', ChargeNotSucceededError],
  ['PlanUnchangedError', PlanUnchangedError],
  ['PlanChangePendingError', PlanChangePendingError],
  ['PlanManagedByOperatorError', PlanManagedByOperatorError],
  ['PaymentMethodRequiredError', PaymentMethodRequiredError],
  ['BillingAccountConflictError', BillingAccountConflictError],
  ['UnknownPlanError', UnknownPlanError],
  ['PlanRequiresSalesError', PlanRequiresSalesError],
  ['AutomaticTopUpNotAvailableError', AutomaticTopUpNotAvailableError],
] as const) {
  registerV2ErrorClass(key, errorClass)
}
