/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosError } from 'axios'
import { CODE_STATUS } from '../codes.js'
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

export interface ErrorEnvelope {
  readonly message?: unknown
  readonly error?: unknown
  readonly source?: string
  readonly code?: string
  readonly trace_id?: string
  readonly retry_after_seconds?: number
  readonly feature?: string
  readonly entitlement?: string
  readonly replacement?: string
  readonly firstAvailableCursor?: string
  /** `DAYTONA_ROUTER` `REGION_MISMATCH`: the region the request stated. */
  readonly stated?: string
  /** `DAYTONA_ROUTER` `REGION_MISMATCH`: the region actually in effect. */
  readonly actual?: string
  /** `DAYTONA_ROUTER` `UNKNOWN_REGION`: the unrecognized region. */
  readonly region?: string
  /** `LIMIT_EXCEEDED`: machine name of the exhausted organization limit. */
  readonly limit?: unknown
  /** `LIMIT_EXCEEDED`: the organization's current count for that limit. */
  readonly current?: unknown
  /** `LIMIT_EXCEEDED`: the maximum the organization is allowed. */
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

export class DaytonaError extends Error {
  public readonly statusCode?: number
  public readonly code?: ErrorCode | string
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
  public readonly errorCode?: string
  public readonly requestId?: string
  public readonly retryAfterMs?: number

  constructor(message: string, statusCode?: number, code?: string, source?: string, envelope?: ErrorEnvelope) {
    super(message)
    this.name = new.target.name
    this.statusCode = statusCode
    this.code = code
    this.errorCode = code
    this.source = source
    this.traceId = envelope?.trace_id
    this.retryAfterSeconds = envelope?.retry_after_seconds
    this.feature = envelope?.feature
    this.entitlement = envelope?.entitlement
    this.replacement = envelope?.replacement
    this.firstAvailableCursor = envelope?.firstAvailableCursor
    this.stated = typeof envelope?.stated === 'string' ? envelope.stated : undefined
    this.actual = typeof envelope?.actual === 'string' ? envelope.actual : undefined
    this.region = typeof envelope?.region === 'string' ? envelope.region : undefined
    this.requestId = typeof envelope?.requestId === 'string' ? envelope.requestId : undefined
    this.retryAfterMs = typeof envelope?.retryAfterMs === 'number' && Number.isInteger(envelope.retryAfterMs) && envelope.retryAfterMs >= 0
      ? envelope.retryAfterMs : undefined
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
export class AuthorizationError extends DaytonaError {}
export class NotFoundError extends DaytonaError {}
export class RequestTimeoutError extends DaytonaError {}

/**
 * `create()` ran out of caller budget while the sandbox was still starting.
 * The sandbox was created and is left running; `sandboxId` lets the caller
 * pick it up (`daytona.get(sandboxId)`) or delete it.
 */
export class SandboxCreateTimeoutError extends RequestTimeoutError {
  public readonly sandboxId: string

  constructor(sandboxId: string) {
    super(`sandbox ${sandboxId} did not start within the requested timeout`, 408, 'REQUEST_TIMEOUT', 'SDK')
    this.sandboxId = sandboxId
  }
}
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

  constructor(status: DockerStatus | undefined) {
    super(`docker did not become ready within the requested timeout${status ? ` (last state: ${status.rawState || status.state})` : ''}`, 408, 'REQUEST_TIMEOUT', 'SDK')
    this.status = status
  }
}

/**
 * `waitForState()` saw the sandbox reach a terminal state before any target.
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

  constructor(sandboxId: string, state: SandboxState, rawState?: string, reason?: string) {
    super(`sandbox ${sandboxId} reached terminal state ${state}${reason ? `: ${reason}` : ''}`, 409, 'CONFLICT', 'SDK')
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
    super(`docker is ${status.rawState || status.state}${status.reason ? `: ${status.reason}` : ''}`, 409, 'CONFLICT', 'SDK')
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
export class RateLimitError extends DaytonaError {}

/**
 * `LIMIT_EXCEEDED` (429): an organization limit is exhausted.
 * Subclasses {@link RateLimitError}, so `catch`/`instanceof RateLimitError` keeps
 * matching; `limit` is the limit's stable machine name (e.g. `max_concurrent_sandboxes`)
 * and `current`/`max` are the counts the refusal was computed from.
 * Fields are `undefined` when a body omits or malforms them.
 */
export class LimitExceededError extends RateLimitError {
  public readonly limit?: string
  public readonly current?: number
  public readonly max?: number

  constructor(message: string, statusCode?: number, code?: string, source?: string, envelope?: ErrorEnvelope) {
    super(message, statusCode, code ?? 'LIMIT_EXCEEDED', source, envelope)
    this.limit = typeof envelope?.limit === 'string' ? envelope.limit : undefined
    this.current = limitCount(envelope?.current)
    this.max = limitCount(envelope?.max)
  }
}

class ArtifactTooLargeError extends ConflictError {
  public readonly maxBytes?: number
  public readonly bytes?: number

  constructor(message: string, statusCode?: number, code?: string, source?: string, envelope?: ErrorEnvelope) {
    super(message, statusCode, code, source, envelope)
    this.maxBytes = limitCount(envelope?.max_bytes)
    this.bytes = limitCount(envelope?.bytes)
  }
}

export class ImageTooLargeError extends ArtifactTooLargeError {}
export class SnapshotTooLargeError extends ArtifactTooLargeError {}

export class PreconditionRequiredError extends DaytonaError {}
export class ConnectionError extends DaytonaError {}
export class TimeoutError extends DaytonaError {}
export class TransferCancelledError extends DaytonaError {
  constructor(message = 'file transfer cancelled') {
    super(message, 499, 'REQUEST_CANCELLED', 'SDK')
  }
}

/**
 * The `create()` request itself timed out before the server answered. The
 * server may still have created the sandbox. `idempotencyKey` is the key the
 * request was sent with. Retry with that key and identical create options, in
 * the same project and API endpoint, to recover the sandbox (or create
 * it if the first request never landed). Always reuse the original create options.
 */
export class SandboxCreateRequestTimeoutError extends TimeoutError {
  public readonly idempotencyKey: string

  constructor(idempotencyKey: string) {
    super(
      `sandbox create request timed out before the server answered; retry create() with idempotencyKey=${JSON.stringify(idempotencyKey)} to reconcile`,
      408,
      'REQUEST_TIMEOUT',
      'SDK',
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
      undefined,
      undefined,
      'SDK',
    )
    this.idempotencyKey = idempotencyKey
  }
}
export class NotSupportedError extends InternalServerError {}

export class ProcessNotFoundError extends NotFoundError {}
export class ProcessStartFailedError extends BadRequestError {}
export class ProcessTerminalError extends ConflictError {}
export class ProcessExecutionTimeoutError extends RequestTimeoutError {}
export class NameConflictError extends ConflictError {}
export class InvalidCursorError extends BadRequestError {}
export class CursorExpiredError extends ConflictError {}
export class StdinClosedError extends ConflictError {}
export class StdinUnavailableError extends ConflictError {}
export class ProtectedProcessError extends AuthorizationError {}
export class UnsupportedOperationError extends BadRequestError {}
export class SessionNotFoundError extends NotFoundError {}
export class SessionEndedError extends GoneError {}
export class KernelUnavailableError extends ServiceUnavailableError {}
export class LspServerUnavailableError extends ServiceUnavailableError {}
export class LspNotStartedError extends ConflictError {}
export class AddonUnavailableError extends ServiceUnavailableError {}
export class FileNotFoundError extends NotFoundError {}
export class InvalidFilePathError extends BadRequestError {}
export class FileAccessDeniedError extends AuthorizationError {}
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
export class RateLimitedError extends RateLimitError {}
export class OverloadedError extends RateLimitError {}
export class NotSupportedForClassError extends ConflictError {}
export class FeatureNotEnabledError extends AuthorizationError {}

/** `IDLE_OUT_OF_RANGE` / `CREATE_TTL_OUT_OF_RANGE` (400): `max` is the server's ceiling for the knob. */
class MaxBoundError extends BadRequestError {
  public readonly max?: number

  constructor(message: string, statusCode?: number, code?: string, source?: string, envelope?: ErrorEnvelope) {
    super(message, statusCode, code, source, envelope)
    this.max = limitCount(envelope?.max)
  }
}

export class ImageNotFoundError extends NotFoundError {}
export class ImageNotReadyError extends ConflictError {}
/**
 * `IMAGE_FORMAT_OUTDATED` (409): the image is stored in an older format than
 * the current one (`Image.formatOutdated`) and requires conversion.
 * Convert it with `POST /images/{id}/convert`, or create a Linux sandbox from
 * it, which converts it first.
 */
export class ImageFormatOutdatedError extends ConflictError {}
export class ImageBuildFailedError extends ConflictError {
  public readonly imageId?: string
  public readonly buildError: string

  constructor(message: string, statusCode?: number, code?: string, source?: string, envelope?: ErrorEnvelope, imageId?: string) {
    super(message, statusCode, code, source, envelope)
    this.imageId = imageId
    this.buildError = message
  }
}
export class ImageClassMismatchError extends ConflictError {}
export class ImageNameTakenError extends ConflictError {}
export class ImageNameReservedError extends BadRequestError {}
export class IdleOutOfRangeError extends MaxBoundError {}
export class CreateTtlOutOfRangeError extends MaxBoundError {}
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
  REGION_UNAVAILABLE: ConflictError,
  ECR_ACCOUNT_MISMATCH: BadRequestError,
  ECR_CREDENTIALS_INVALID: BadRequestError,
  ECR_OWNERSHIP_UNVERIFIABLE: InternalServerError,
  EMAIL_ACCESS_DENIED: AuthorizationError,
  ENTITLEMENT_REQUIRED: AuthorizationError,
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
  RATE_LIMITED: RateLimitedError,
  SEARCH_NOT_CONFIGURED: SearchNotConfiguredError,
  SEARCH_UNAVAILABLE: SearchUnavailableError,
  SIGNUP_NOT_INVITED: AuthorizationError,
  USER_SUSPENDED: AuthorizationError,
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
  V0_MIGRATION_DEFAULT_PROJECT_ONLY: AuthorizationError,
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
  FORBIDDEN: AuthorizationError,
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
  403: AuthorizationError,
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

function limitCount(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) ? value : undefined
}

function ownClass<T extends object>(map: T | undefined, key: string): T[keyof T] | undefined {
  return map && Object.prototype.hasOwnProperty.call(map, key) ? map[key as keyof T] : undefined
}

export function createDaytonaError(message: string, statusCode?: number, code?: string, source?: string, envelope?: ErrorEnvelope): DaytonaError {
  const ErrorClass =
    ownClass(ownClass(SOURCE_ERROR_CLASS, source ?? ''), code ?? '') ??
    ownClass(GENERIC_ERROR_CLASS, code ?? '') ??
    errorClassFromStatusCode(statusCode)
  return new ErrorClass(message, statusCode, code, source, envelope)
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

export function createAxiosDaytonaError(error: AxiosError): DaytonaError {
  const envelope = dataObject(error.response?.data)
  const status = error.response?.status
  const code = typeof envelope?.code === 'string' ? envelope.code : undefined
  const source = typeof envelope?.source === 'string' ? envelope.source : undefined
  if (isAxiosTimeoutError(error)) return new TimeoutError('Operation timed out', status, code, source, envelope)
  if (!error.response && (error.request || error.code)) return new ConnectionError(error.message, status, code, source, envelope)
  const fallback = typeof error.response?.data === 'string' ? error.response.data : error.message
  return createDaytonaError(messageFromEnvelope(envelope, fallback), status, code, source, envelope)
}

// Reads the unified envelope off a string body for callers that read the body
// themselves (build logs, with their own size/deadline bounds), so they
// classify errors the same way createAxiosDaytonaError does. Not an envelope,
// or not JSON, falls back to fallbackMessage + status-level dispatch.
// emptyProseIsAbsent makes an empty-string message/error use the fallback too,
// so the build-log readers agree with the Python SDK on a prose-less body.
export function errorFromEnvelopeBody(
  body: string,
  status: number,
  fallbackMessage: string,
  emptyProseIsAbsent = false,
): DaytonaError {
  let parsed: ErrorEnvelope | undefined
  try {
    parsed = dataObject(JSON.parse(body) as unknown)
  } catch {
    parsed = undefined
  }
  const code = typeof parsed?.code === 'string' ? parsed.code : undefined
  const source = typeof parsed?.source === 'string' ? parsed.source : undefined
  // emptyProseIsAbsent picks the first non-empty of message/error before the
  // fallback — matching Python's `message or error or fallback` so an empty
  // message never masks a non-empty error.
  const message = emptyProseIsAbsent
    ? (typeof parsed?.message === 'string' && parsed.message) || (typeof parsed?.error === 'string' && parsed.error) || fallbackMessage
    : messageFromEnvelope(parsed, fallbackMessage)
  return createDaytonaError(message, status, code, source, parsed)
}

export async function errorFromFetchResponse(response: Response): Promise<DaytonaError> {
  const text = await response.text()
  return errorFromEnvelopeBody(text, response.status, text || response.statusText)
}

export const DaytonaNotFoundError = NotFoundError
export const DaytonaAuthenticationError = AuthenticationError
export const DaytonaAuthorizationError = AuthorizationError
export const DaytonaConflictError = ConflictError
export const DaytonaValidationError = BadRequestError
export const DaytonaRateLimitError = RateLimitError
export const DaytonaTimeoutError = TimeoutError
export const DaytonaConnectionError = ConnectionError
export const DaytonaNotSupportedError = NotSupportedError

export function statusForCode(code: ErrorCode): number {
  return CODE_STATUS[code]
}

registerErrorClasses(RootDaytonaError, [DaytonaError])
registerErrorClasses(RootDaytonaBadRequestError, [BadRequestError])
registerErrorClasses(RootDaytonaAuthenticationError, [AuthenticationError])
registerErrorClasses(RootDaytonaForbiddenError, [AuthorizationError])
registerErrorClasses(RootDaytonaNotFoundError, [NotFoundError])
registerErrorClasses(RootDaytonaTimeoutError, [RequestTimeoutError, TimeoutError])
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
  ['AuthorizationError', AuthorizationError],
  ['NotFoundError', NotFoundError],
  ['RequestTimeoutError', RequestTimeoutError],
  ['SandboxCreateTimeoutError', SandboxCreateTimeoutError],
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
  ['TimeoutError', TimeoutError],
  ['TransferCancelledError', TransferCancelledError],
  ['SandboxCreateRequestTimeoutError', SandboxCreateRequestTimeoutError],
  ['SandboxCreateConnectionError', SandboxCreateConnectionError],
  ['NotSupportedError', NotSupportedError],
  ['ProcessNotFoundError', ProcessNotFoundError],
  ['ProcessStartFailedError', ProcessStartFailedError],
  ['ProcessTerminalError', ProcessTerminalError],
  ['ProcessExecutionTimeoutError', ProcessExecutionTimeoutError],
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
  ['RateLimitedError', RateLimitedError],
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
