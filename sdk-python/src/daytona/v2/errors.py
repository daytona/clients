# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Type

from typing_extensions import TypedDict, Unpack

from .. import _errors as _shared
from . import _codes as codes

if TYPE_CHECKING:
    from .types import DockerStatus, SandboxState


def _docker_status_label(status: DockerStatus) -> str:
    return status.raw_state or status.state


class _ErrorFields(TypedDict, total=False):
    """The optional envelope fields every error constructor accepts (internal)."""

    trace_id: Optional[str]
    retry_after_seconds: Optional[int]
    feature: Optional[str]
    entitlement: Optional[str]
    replacement: Optional[str]
    first_available_cursor: Optional[str]
    limit: Optional[str]
    current: Optional[int]
    max: Optional[int]
    max_bytes: Optional[int]
    bytes: Optional[int]
    stated: Optional[str]
    actual: Optional[str]
    region: Optional[str]
    request_id: Optional[str]
    retry_after_ms: Optional[int]


class DaytonaError(_shared.DaytonaError):
    """Base class for all v2 SDK errors.

    ``code`` and ``source`` are open vocabularies: the server may send a code
    or source this SDK release does not know yet. An unknown code keeps its
    exact value on ``code`` and the error falls back to the class for its HTTP
    status (``NotFoundError`` for a 404, ...). ``daytona.v2.codes`` lists the
    codes this release knows.

    Every keyword is optional; an error built by the caller carries only what
    it is given. A subclass with defaults (``ClientTimeoutError``, ...) uses its
    default for a keyword that is omitted or ``None``, as the TypeScript SDK does
    for ``undefined``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        source: Optional[str] = None,
        trace_id: Optional[str] = None,
        retry_after_seconds: Optional[int] = None,
        feature: Optional[str] = None,
        entitlement: Optional[str] = None,
        replacement: Optional[str] = None,
        first_available_cursor: Optional[str] = None,
        limit: Optional[str] = None,
        current: Optional[int] = None,
        max: Optional[int] = None,
        max_bytes: Optional[int] = None,
        bytes: Optional[int] = None,
        stated: Optional[str] = None,
        actual: Optional[str] = None,
        region: Optional[str] = None,
        request_id: Optional[str] = None,
        retry_after_ms: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.source = source
        self.trace_id = trace_id
        self.retry_after_seconds = retry_after_seconds
        self.feature = feature
        self.entitlement = entitlement
        self.replacement = replacement
        self.first_available_cursor = first_available_cursor
        self.limit = limit
        self.current = current
        self.max = max
        self.max_bytes = max_bytes
        self.bytes = bytes
        self.stated = stated
        self.actual = actual
        self.region = region
        self.request_id = request_id
        self.retry_after_ms = retry_after_ms


class BadRequestError(DaytonaError, _shared.DaytonaBadRequestError):
    """HTTP 400."""


class AuthenticationError(DaytonaError, _shared.DaytonaAuthenticationError):
    """HTTP 401."""


class ForbiddenError(DaytonaError, _shared.DaytonaForbiddenError):
    """HTTP 403."""


class NotFoundError(DaytonaError, _shared.DaytonaNotFoundError):
    """HTTP 404."""


class RequestTimeoutError(DaytonaError, _shared.DaytonaTimeoutError):
    """HTTP 408."""


class DockerWaitTimeoutError(RequestTimeoutError):
    """``sandbox.docker.wait_until_ready()`` ran out of caller budget before Docker was ``ready``.

    ``status`` is the last observed Docker status (``starting``, or
    ``deferred`` while the boot has not started Docker yet), or ``None`` when
    no status answer arrived before the deadline (for example a stalled first
    request).
    """

    def __init__(self, status: Optional[DockerStatus]) -> None:
        suffix = f" (last state: {_docker_status_label(status)})" if status is not None else ""
        super().__init__(
            f"docker did not become ready within the requested timeout{suffix}",
            status_code=408,
            code="REQUEST_TIMEOUT",
            source="SDK",
        )
        self.status: Optional[DockerStatus] = status


class ConflictError(DaytonaError, _shared.DaytonaConflictError):
    """HTTP 409."""


class DockerNotReadyError(ConflictError):
    """``sandbox.docker.wait_until_ready()`` saw Docker settle in a state that cannot become ``ready``.

    The terminal states are ``failed``, ``not_present``, ``disabled``,
    ``unmanaged`` and ``unsupported``. ``status`` is the observed Docker
    status; its ``reason`` says why, and ``process_id`` (when set) names the
    process whose logs hold Docker's output. The sandbox itself stays usable.
    """

    def __init__(self, status: DockerStatus) -> None:
        reason = f": {status.reason}" if status.reason else ""
        super().__init__(
            f"docker is {_docker_status_label(status)}{reason}",
            status_code=409,
            code="CONFLICT",
            source="SDK",
        )
        self.status: DockerStatus = status


class SandboxTerminalStateError(ConflictError):
    """A lifecycle wait (``wait()``, ``start()``, ``stop()``, ...) saw the sandbox reach a terminal state first.

    ``state`` is the observed public sandbox state (e.g. ``"destroyed"``).
    ``reason`` is the server's explanation of that state, when it gave one
    (for example why a deletion did not finish); the message ends with it.
    """

    def __init__(
        self,
        sandbox_id: str,
        state: SandboxState,
        *,
        raw_state: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        message = f"sandbox {sandbox_id} reached terminal state {state}"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(
            message,
            status_code=409,
            code="CONFLICT",
            source="SDK",
        )
        self.state: SandboxState = state
        self.raw_state: Optional[str] = raw_state
        self.reason: Optional[str] = reason or None


class PreconditionFailedError(DaytonaError):
    """HTTP 412 — the ``If-Match`` revision no longer matches (network policy CAS)."""


class SecretAlreadyExistsError(PreconditionFailedError):
    """A create-only secret write found an existing secret."""


class GoneError(DaytonaError, _shared.DaytonaGoneError):
    """HTTP 410."""


class MethodNotAllowedError(DaytonaError):
    """HTTP 405."""


class PayloadTooLargeError(DaytonaError):
    """HTTP 413."""


class UnsupportedMediaTypeError(DaytonaError):
    """HTTP 415."""


class UnprocessableEntityError(DaytonaError, _shared.DaytonaUnprocessableEntityError):
    """HTTP 422."""


class RateLimitError(DaytonaError, _shared.DaytonaRateLimitError):
    """HTTP 429. ``RATE_LIMITED`` and ``TOO_MANY_REQUESTS`` raise this class
    itself (read ``code`` to tell them apart); ``LIMIT_EXCEEDED`` and
    ``OVERLOADED`` raise its subclasses."""


class LimitExceededError(RateLimitError):
    """``LIMIT_EXCEEDED`` (429): an organization limit is exhausted.

    Subclasses :class:`RateLimitError`, so ``except RateLimitError`` keeps matching;
    ``limit`` is the limit's stable machine name (e.g. ``max_concurrent_sandboxes``) and
    ``current``/``max`` are the counts the refusal was computed from. Attributes are
    ``None`` when a body omits or malforms them. ``code`` defaults to ``"LIMIT_EXCEEDED"``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        source: Optional[str] = None,
        **fields: Unpack[_ErrorFields],
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            code=code if code is not None else "LIMIT_EXCEEDED",
            source=source,
            **fields,
        )


class ImageTooLargeError(ConflictError):
    pass


class SnapshotTooLargeError(ConflictError):
    pass


class PreconditionRequiredError(DaytonaError):
    pass


class ServiceUnavailableError(DaytonaError, _shared.DaytonaServiceUnavailableError):
    """HTTP 503."""


class InternalServerError(DaytonaError, _shared.DaytonaInternalServerError):
    """HTTP 5xx."""


class NotSupportedError(InternalServerError):
    pass


class ConnectionError(DaytonaError, _shared.DaytonaConnectionError):
    """The API or the sandbox could not be reached (no HTTP response).

    ``source`` defaults to ``"SDK"``; there is no status or code.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        source: Optional[str] = None,
        **fields: Unpack[_ErrorFields],
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            code=code,
            source=source if source is not None else "SDK",
            **fields,
        )


class ClientTimeoutError(DaytonaError, _shared.DaytonaTimeoutError):
    """The client gave up waiting for a response.

    Its request timeout or a call's own deadline expired; not the server's
    HTTP 408 (:class:`RequestTimeoutError`). Defaults: message
    ``"operation timed out"``, ``status_code=408``, ``code="REQUEST_TIMEOUT"``,
    ``source="SDK"`` (the same in the TypeScript SDK).
    """

    def __init__(
        self,
        message: str = "operation timed out",
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        source: Optional[str] = None,
        **fields: Unpack[_ErrorFields],
    ) -> None:
        super().__init__(
            message,
            status_code=status_code if status_code is not None else 408,
            code=code if code is not None else "REQUEST_TIMEOUT",
            source=source if source is not None else "SDK",
            **fields,
        )


class TransferCancelledError(DaytonaError):
    """A local file transfer was cancelled before completion.

    Defaults: message ``"file transfer cancelled"``, ``status_code=499``,
    ``code="REQUEST_CANCELLED"``, ``source="SDK"``.
    """

    def __init__(
        self,
        message: str = "file transfer cancelled",
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        source: Optional[str] = None,
        **fields: Unpack[_ErrorFields],
    ) -> None:
        super().__init__(
            message,
            status_code=status_code if status_code is not None else 499,
            code=code if code is not None else "REQUEST_CANCELLED",
            source=source if source is not None else "SDK",
            **fields,
        )


class SandboxCreateRequestTimeoutError(ClientTimeoutError):
    """The ``create()`` request itself timed out before the server answered.

    The server may still have created the sandbox. ``idempotency_key`` is the
    key the request was sent with. Retry with that key and the identical source
    and create arguments, in the same project and API endpoint, to
    recover the sandbox (or create it if the first request never landed).
    Always retry with identical arguments: a changed request may be rejected
    or return the originally created sandbox.
    """

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            "sandbox create request timed out before the server answered; "
            f"retry create() with idempotency_key={idempotency_key!r} to reconcile",
            status_code=408,
            code="REQUEST_TIMEOUT",
            source="SDK",
        )
        self.idempotency_key = idempotency_key


class SandboxCreateConnectionError(ConnectionError):
    """The connection failed while the ``create()`` request was in flight.

    Same outcome as :class:`SandboxCreateRequestTimeoutError`: the sandbox may
    or may not exist; retry ``create()`` with the same ``idempotency_key``, source
    and create arguments, in the same project and API endpoint.
    """

    def __init__(self, idempotency_key: str, detail: str) -> None:
        super().__init__(
            f"sandbox create request failed before the server answered ({detail}); "
            f"retry create() with idempotency_key={idempotency_key!r} to reconcile",
            source="SDK",
        )
        self.idempotency_key = idempotency_key


class ProcessNotFoundError(NotFoundError):
    """PROCESS_NOT_FOUND."""


class ProcessStartFailedError(BadRequestError):
    """PROCESS_START_FAILED."""


class ProcessTerminalError(ConflictError):
    """PROCESS_TERMINAL."""


class ProcessExecutionTimeoutError(RequestTimeoutError):
    """PROCESS_EXECUTION_TIMEOUT."""


class ProcessKillIncompleteError(RequestTimeoutError):
    """PROCESS_KILL_INCOMPLETE."""


class NameConflictError(ConflictError):
    """NAME_CONFLICT."""


class InvalidCursorError(BadRequestError):
    """INVALID_CURSOR."""


class CursorExpiredError(ConflictError):
    """CURSOR_EXPIRED."""


class StdinClosedError(ConflictError):
    """STDIN_CLOSED."""


class StdinUnavailableError(ConflictError):
    """STDIN_UNAVAILABLE."""


class ProtectedProcessError(ForbiddenError):
    """PROTECTED_PROCESS."""


class UnsupportedOperationError(BadRequestError):
    """UNSUPPORTED_OPERATION."""


class SessionNotFoundError(NotFoundError):
    """SESSION_NOT_FOUND."""


class SessionEndedError(GoneError):
    """SESSION_ENDED."""


class KernelUnavailableError(ServiceUnavailableError):
    """KERNEL_UNAVAILABLE."""


class LspServerUnavailableError(ServiceUnavailableError):
    """LSP_SERVER_UNAVAILABLE."""


class LspNotStartedError(ConflictError):
    """LSP_NOT_STARTED."""


class AddonUnavailableError(ServiceUnavailableError):
    """ADDON_UNAVAILABLE."""


class FileNotFoundError(NotFoundError):
    """FILE_NOT_FOUND."""


class InvalidFilePathError(BadRequestError):
    """INVALID_FILE_PATH."""


class FileAccessDeniedError(ForbiddenError):
    """FILE_ACCESS_DENIED."""


class FileReadFailedError(InternalServerError):
    """FILE_READ_FAILED."""


class GitAuthFailedError(AuthenticationError):
    """GIT_AUTH_FAILED."""


class GitRepoNotFoundError(NotFoundError):
    """GIT_REPO_NOT_FOUND."""


class GitBranchNotFoundError(NotFoundError):
    """GIT_BRANCH_NOT_FOUND."""


class GitBranchExistsError(ConflictError):
    """GIT_BRANCH_EXISTS."""


class DesktopNotReadyError(ConflictError):
    """DESKTOP_NOT_READY."""


class AtomicUploadUnsupportedError(ConflictError):
    """ATOMIC_UPLOAD_UNSUPPORTED."""


class OrgSuspendedError(DaytonaError):
    pass


class ProjectBudgetExceededError(DaytonaError):
    """PROJECT_BUDGET_EXCEEDED (402): the project reached its monthly budget.

    New sandboxes cannot start in the project until the budget is raised or
    removed, or the next calendar month (UTC) begins.
    """


class OverloadedError(RateLimitError):
    pass


class NotSupportedForClassError(ConflictError):
    pass


class FeatureNotEnabledError(ForbiddenError):
    pass


class UnsupportedVolumeTypeError(BadRequestError):
    pass


class BucketCreateFailedError(InternalServerError):
    pass


class BucketDeleteFailedError(InternalServerError):
    pass


class BucketNotEmptyError(ConflictError):
    pass


class OriginImportOnlyError(BadRequestError):
    pass


class ReadOnlyUnsupportedError(BadRequestError):
    pass


class BillingDisabledError(NotSupportedError):
    pass


class BillingUnreachableError(InternalServerError):
    pass


class BillingUnavailableError(ServiceUnavailableError):
    pass


class BillingNotProvisionedError(ConflictError):
    pass


class ChargeNotFoundError(NotFoundError):
    pass


class InvoiceNotFoundError(NotFoundError):
    pass


class CouponNotFoundError(NotFoundError):
    pass


class CouponExpiredError(ConflictError):
    pass


class CouponLimitReachedError(ConflictError):
    pass


class CouponAlreadyRedeemedError(ConflictError):
    pass


class InvoiceNotFinalizedError(ConflictError):
    pass


class InvoiceNotPayableError(ConflictError):
    pass


class ChargeNotSucceededError(ConflictError):
    pass


class PlanUnchangedError(ConflictError):
    pass


class PlanChangePendingError(ConflictError):
    pass


class PlanManagedByOperatorError(ConflictError):
    pass


class PaymentMethodRequiredError(ConflictError):
    pass


class BillingAccountConflictError(ConflictError):
    pass


class UnknownPlanError(BadRequestError):
    pass


class PlanRequiresSalesError(BadRequestError):
    pass


class AutomaticTopUpNotAvailableError(ConflictError):
    pass


class SearchNotConfiguredError(NotSupportedError):
    pass


class SearchUnavailableError(ServiceUnavailableError):
    pass


class SandboxNotRunningError(ConflictError):
    pass


class ImageNotFoundError(NotFoundError):
    pass


class ImageNotReadyError(ConflictError):
    pass


class ImageFormatOutdatedError(ConflictError):
    """``IMAGE_FORMAT_OUTDATED`` (409): the image is stored in an older format
    than the current one (``Image.format_outdated``) and this operation cannot
    convert it. Convert it with ``POST /images/{id}/convert``, or create a
    Linux sandbox from it, which converts it first."""


class ImageBuildFailedError(ConflictError):
    """``IMAGE_BUILD_FAILED`` (409), or the SDK-minted refusal when
    ``create_from_build()``'s image build ends ``failed``. ``image_id`` names
    the failed image (so its build log can be fetched) when the SDK knows it;
    ``build_error`` is the build's recorded error, when the image has one. It is
    separate from ``message``, which is always set.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        source: Optional[str] = None,
        image_id: Optional[str] = None,
        build_error: Optional[str] = None,
        **fields: Unpack[_ErrorFields],
    ) -> None:
        super().__init__(message, status_code=status_code, code=code, source=source, **fields)
        self.image_id = image_id
        self.build_error = build_error


class ImageClassMismatchError(ConflictError):
    pass


class ImageNameTakenError(ConflictError):
    pass


class ImageNameReservedError(BadRequestError):
    pass


class IdleOutOfRangeError(BadRequestError):
    pass


class CreateTtlOutOfRangeError(BadRequestError):
    pass


class SnapshotNotFoundError(NotFoundError):
    pass


class SnapshotNotReadyError(ConflictError):
    pass


class SnapshotClassMismatchError(ConflictError):
    pass


STATUS_ERROR_CLASSES: Dict[int, Type[DaytonaError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: ForbiddenError,
    404: NotFoundError,
    405: MethodNotAllowedError,
    408: RequestTimeoutError,
    409: ConflictError,
    412: PreconditionFailedError,
    410: GoneError,
    413: PayloadTooLargeError,
    415: UnsupportedMediaTypeError,
    422: UnprocessableEntityError,
    428: PreconditionRequiredError,
    429: RateLimitError,
    501: NotSupportedError,
    503: ServiceUnavailableError,
}

DAEMON_ERROR_CLASSES: Dict[str, Type[DaytonaError]] = {
    codes.PROCESS_NOT_FOUND: ProcessNotFoundError,
    codes.PROCESS_START_FAILED: ProcessStartFailedError,
    codes.PROCESS_TERMINAL: ProcessTerminalError,
    codes.PROCESS_EXECUTION_TIMEOUT: ProcessExecutionTimeoutError,
    codes.PROCESS_KILL_INCOMPLETE: ProcessKillIncompleteError,
    codes.NAME_CONFLICT: NameConflictError,
    codes.INVALID_CURSOR: InvalidCursorError,
    codes.CURSOR_EXPIRED: CursorExpiredError,
    codes.STDIN_CLOSED: StdinClosedError,
    codes.STDIN_UNAVAILABLE: StdinUnavailableError,
    codes.PROTECTED_PROCESS: ProtectedProcessError,
    codes.UNSUPPORTED_OPERATION: UnsupportedOperationError,
    codes.SESSION_NOT_FOUND: SessionNotFoundError,
    codes.SESSION_ENDED: SessionEndedError,
    codes.KERNEL_UNAVAILABLE: KernelUnavailableError,
    codes.LSP_SERVER_UNAVAILABLE: LspServerUnavailableError,
    codes.LSP_NOT_STARTED: LspNotStartedError,
    codes.ADDON_UNAVAILABLE: AddonUnavailableError,
    codes.FILE_NOT_FOUND: FileNotFoundError,
    codes.INVALID_FILE_PATH: InvalidFilePathError,
    codes.FILE_ACCESS_DENIED: FileAccessDeniedError,
    codes.FILE_READ_FAILED: FileReadFailedError,
    codes.GIT_AUTH_FAILED: GitAuthFailedError,
    codes.GIT_REPO_NOT_FOUND: GitRepoNotFoundError,
    codes.GIT_BRANCH_NOT_FOUND: GitBranchNotFoundError,
    codes.GIT_BRANCH_EXISTS: GitBranchExistsError,
    codes.DESKTOP_NOT_READY: DesktopNotReadyError,
    codes.ATOMIC_UPLOAD_UNSUPPORTED: AtomicUploadUnsupportedError,
}

API_ERROR_CLASSES: Dict[str, Type[DaytonaError]] = {
    codes.BILLING_DISABLED: BillingDisabledError,
    codes.BILLING_UNREACHABLE: BillingUnreachableError,
    codes.BILLING_UNAVAILABLE: BillingUnavailableError,
    codes.BILLING_NOT_PROVISIONED: BillingNotProvisionedError,
    codes.CHARGE_NOT_FOUND: ChargeNotFoundError,
    codes.INVOICE_NOT_FOUND: InvoiceNotFoundError,
    codes.COUPON_NOT_FOUND: CouponNotFoundError,
    codes.COUPON_EXPIRED: CouponExpiredError,
    codes.COUPON_LIMIT_REACHED: CouponLimitReachedError,
    codes.COUPON_ALREADY_REDEEMED: CouponAlreadyRedeemedError,
    codes.INVOICE_NOT_FINALIZED: InvoiceNotFinalizedError,
    codes.INVOICE_NOT_PAYABLE: InvoiceNotPayableError,
    codes.CHARGE_NOT_SUCCEEDED: ChargeNotSucceededError,
    codes.PLAN_UNCHANGED: PlanUnchangedError,
    codes.PLAN_CHANGE_PENDING: PlanChangePendingError,
    codes.PLAN_MANAGED_BY_OPERATOR: PlanManagedByOperatorError,
    codes.PAYMENT_METHOD_REQUIRED: PaymentMethodRequiredError,
    codes.BILLING_ACCOUNT_CONFLICT: BillingAccountConflictError,
    codes.UNKNOWN_PLAN: UnknownPlanError,
    codes.PLAN_REQUIRES_SALES: PlanRequiresSalesError,
    codes.AUTOMATIC_TOP_UP_NOT_AVAILABLE: AutomaticTopUpNotAvailableError,
    codes.BUCKET_CREATE_FAILED: BucketCreateFailedError,
    codes.BUCKET_DELETE_FAILED: BucketDeleteFailedError,
    codes.BUCKET_NOT_EMPTY: BucketNotEmptyError,
    codes.CREATE_BODY_TOO_LARGE: PayloadTooLargeError,
    codes.INVALID_PARENT_REGION: BadRequestError,
    codes.INVALID_PREVIEW_DOMAIN: BadRequestError,
    codes.INVALID_SANDBOX_CIDR: BadRequestError,
    codes.INVALID_TOOLBOX_PROXY_URL: BadRequestError,
    codes.REGION_LIMIT_REACHED: ConflictError,
    codes.REGION_NOT_EMPTY: ConflictError,
    codes.REGION_NOT_FOUND: NotFoundError,
    codes.REGION_SUBNETS_EXHAUSTED: ConflictError,
    codes.REGION_UNAVAILABLE: ConflictError,
    codes.RUNNER_HAS_SANDBOXES: ConflictError,
    codes.DISK_NOT_READY: ConflictError,
    codes.ROLE_IN_USE: ConflictError,
    codes.ECR_ACCOUNT_MISMATCH: BadRequestError,
    codes.ECR_CREDENTIALS_INVALID: BadRequestError,
    codes.ECR_OWNERSHIP_UNVERIFIABLE: InternalServerError,
    codes.EMAIL_ACCESS_DENIED: ForbiddenError,
    codes.ENTITLEMENT_REQUIRED: ForbiddenError,
    codes.FEATURE_NOT_ENABLED: FeatureNotEnabledError,
    codes.INVALID_FOR_SOURCE: BadRequestError,
    codes.LIMIT_EXCEEDED: LimitExceededError,
    codes.USAGE_UNAVAILABLE: ServiceUnavailableError,
    codes.BAD_RANGE: BadRequestError,
    codes.BAD_RESOLUTION: BadRequestError,
    codes.BILLING_FORBIDDEN: InternalServerError,
    codes.BILLING_ERROR: InternalServerError,
    codes.IF_MATCH_REQUIRED: PreconditionRequiredError,
    codes.SECRETS_TRANSITION_IN_PROGRESS: ConflictError,
    codes.STALE_SECRETS_REVISION: PreconditionFailedError,
    codes.MANAGEMENT_PLANE_ELSEWHERE: NotSupportedError,
    codes.NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL: BadRequestError,
    codes.NETWORK_POLICY_TRANSITION_IN_PROGRESS: ConflictError,
    codes.STALE_NETWORK_POLICY_REVISION: PreconditionFailedError,
    codes.NOT_SUPPORTED_FOR_CLASS: NotSupportedForClassError,
    codes.IMAGE_NOT_FOUND: ImageNotFoundError,
    codes.IMAGE_NOT_READY: ImageNotReadyError,
    codes.IMAGE_BUILD_FAILED: ImageBuildFailedError,
    codes.DEFAULT_IMAGE_NOT_CONFIGURED: BadRequestError,
    codes.IMAGE_FORMAT_OUTDATED: ImageFormatOutdatedError,
    codes.IMAGE_CLASS_MISMATCH: ImageClassMismatchError,
    codes.IMAGE_NAME_TAKEN: ImageNameTakenError,
    codes.IMAGE_NAME_RESERVED: ImageNameReservedError,
    codes.IMAGE_TOO_LARGE: ImageTooLargeError,
    codes.IDLE_OUT_OF_RANGE: IdleOutOfRangeError,
    codes.CREATE_TTL_OUT_OF_RANGE: CreateTtlOutOfRangeError,
    codes.SNAPSHOT_NOT_FOUND: SnapshotNotFoundError,
    codes.SNAPSHOT_NOT_READY: SnapshotNotReadyError,
    codes.SNAPSHOT_CLASS_MISMATCH: SnapshotClassMismatchError,
    codes.SNAPSHOT_IN_PROGRESS: ConflictError,
    codes.SNAPSHOT_TOO_LARGE: SnapshotTooLargeError,
    codes.IMPORTED_KEY_ALWAYS_SYNCED: ConflictError,
    codes.KEY_SYNC_NOT_APPLICABLE: ConflictError,
    codes.ORG_SUSPENDED: OrgSuspendedError,
    codes.ORIGIN_IMPORT_ONLY: OriginImportOnlyError,
    codes.PROJECT_BUDGET_EXCEEDED: ProjectBudgetExceededError,
    codes.OVERLOADED: OverloadedError,
    codes.PLACEMENT_UNAVAILABLE: ServiceUnavailableError,
    codes.RATE_LIMITED: RateLimitError,
    codes.READ_ONLY_UNSUPPORTED: ReadOnlyUnsupportedError,
    codes.SEARCH_NOT_CONFIGURED: SearchNotConfiguredError,
    codes.SEARCH_UNAVAILABLE: SearchUnavailableError,
    codes.SIGNUP_NOT_INVITED: ForbiddenError,
    codes.USER_SUSPENDED: ForbiddenError,
    codes.SOURCE_AMBIGUOUS: ConflictError,
    codes.SECRET_ALREADY_EXISTS: SecretAlreadyExistsError,
    codes.UNSUPPORTED_VOLUME_TYPE: UnsupportedVolumeTypeError,
    codes.WORKLOAD_PLANE_ELSEWHERE: NotSupportedError,
    codes.NO_ACTIVE_RESERVATION: ConflictError,
    codes.NO_FREE_SLOT: ConflictError,
    codes.RESERVATION_NOT_FOUND: NotFoundError,
    codes.NO_MAC_AVAILABLE: ConflictError,
    codes.MAC_DISK_FULL: ConflictError,
    codes.AUTO_RESERVE_LIMIT_REACHED: ConflictError,
    codes.IDEMPOTENCY_KEY_REUSED: ConflictError,
    codes.INSUFFICIENT_BALANCE: DaytonaError,
    codes.CHARGE_FAILED: DaytonaError,
    codes.V0_BRIDGE_UNAVAILABLE: ServiceUnavailableError,
    codes.V0_SANDBOX_NOT_FOUND: NotFoundError,
    codes.V0_SANDBOX_NOT_IMPORTABLE: ConflictError,
    codes.V0_BACKUP_REGISTRY_UNCONFIGURED: ServiceUnavailableError,
    codes.V0_MIGRATION_DEFAULT_PROJECT_ONLY: ForbiddenError,
}

PROXY_ERROR_CLASSES: Dict[str, Type[DaytonaError]] = {
    codes.SANDBOX_NOT_RUNNING: SandboxNotRunningError,
}

ROUTER_ERROR_CLASSES: Dict[str, Type[DaytonaError]] = {
    codes.REGION_MISMATCH: BadRequestError,
    codes.UNKNOWN_REGION: BadRequestError,
    codes.REGION_REQUIRED: BadRequestError,
    codes.IDEMPOTENCY_KEY_REQUIRED: BadRequestError,
    codes.CREATE_BODY_TOO_LARGE: PayloadTooLargeError,
    codes.PLACEMENT_UNAVAILABLE: ServiceUnavailableError,
}

GENERIC_ERROR_CLASSES: Dict[str, Type[DaytonaError]] = {
    codes.BAD_GATEWAY: InternalServerError,
    codes.BAD_REQUEST: BadRequestError,
    codes.CONFLICT: ConflictError,
    codes.FORBIDDEN: ForbiddenError,
    codes.GATEWAY_TIMEOUT: InternalServerError,
    codes.GONE: GoneError,
    codes.INTERNAL_SERVER_ERROR: InternalServerError,
    codes.METHOD_NOT_ALLOWED: MethodNotAllowedError,
    codes.NOT_FOUND: NotFoundError,
    codes.NOT_IMPLEMENTED: NotSupportedError,
    codes.PAYMENT_REQUIRED: DaytonaError,
    codes.PAYLOAD_TOO_LARGE: PayloadTooLargeError,
    codes.REQUEST_TIMEOUT: RequestTimeoutError,
    codes.SERVICE_UNAVAILABLE: ServiceUnavailableError,
    codes.TOO_MANY_REQUESTS: RateLimitError,
    codes.UNAUTHORIZED: AuthenticationError,
    codes.UNPROCESSABLE_ENTITY: UnprocessableEntityError,
    codes.UNSUPPORTED_MEDIA_TYPE: UnsupportedMediaTypeError,
}

CODE_ERROR_CLASSES: Dict[str, Type[DaytonaError]] = {
    **DAEMON_ERROR_CLASSES,
    **API_ERROR_CLASSES,
    **PROXY_ERROR_CLASSES,
    **ROUTER_ERROR_CLASSES,
    **GENERIC_ERROR_CLASSES,
}

SOURCE_ERROR_CLASSES: Dict[str, Dict[str, Type[DaytonaError]]] = {
    "DAYTONA_API": API_ERROR_CLASSES,
    "DAYTONA_DAEMON": DAEMON_ERROR_CLASSES,
    "DAYTONA_PROXY": PROXY_ERROR_CLASSES,
    "DAYTONA_ROUTER": ROUTER_ERROR_CLASSES,
    # SDK-synthesized errors (timeouts, terminal states) reuse the process API's typed
    # classes so a caller's `except` clauses need not branch on who raised.
    # Every SDK-minted code today is generic, so the table is vestigial until
    # the SDK mints a process-specific code. Mirror any change in DaytonaError.ts.
    "SDK": DAEMON_ERROR_CLASSES,
}


def error_class_from_status(status_code: Optional[int]) -> Type[DaytonaError]:
    if status_code is None:
        return DaytonaError
    mapped = STATUS_ERROR_CLASSES.get(status_code)
    if mapped is not None:
        return mapped
    if status_code >= 500:
        return InternalServerError
    return DaytonaError


def create_error(
    message: str,
    status_code: Optional[int] = None,
    code: Optional[str] = None,
    source: Optional[str] = None,
    **fields: Unpack[_ErrorFields],
) -> DaytonaError:
    """The typed error for a ``(source, code)`` pair, else for the status (internal)."""
    source_classes = SOURCE_ERROR_CLASSES.get(source or "", {})
    cls = (
        source_classes.get(code or "")
        or GENERIC_ERROR_CLASSES.get(code or "")
        or error_class_from_status(status_code)
    )
    return cls(message, status_code=status_code, code=code, source=source, **fields)


def redirect_error(status_code: int) -> DaytonaError:
    """The error for a 3xx response (internal): the SDK follows no redirect,
    whatever the body says, as in TypeScript."""
    return create_error(
        f"HTTP {status_code}: unexpected redirect (the SDK does not follow redirects)",
        status_code,
        source="SDK",
    )


# An async operation that is cancelled may still hold a typed outcome the
# synchronous caller should see instead of a bare KeyboardInterrupt (a build-log
# follower's last checkpoint). The cancellation itself propagates unchanged, so
# cancel scopes, timeouts and task groups keep working; the typed error rides
# on it and the sync bridge surfaces it once the cancelled task has unwound.
_INTERRUPT_ERROR_ATTR = "_daytona_interrupt_error"


def _attach_interrupt_error(cancelled: BaseException, error: DaytonaError) -> None:
    setattr(cancelled, _INTERRUPT_ERROR_ATTR, error)


def _interrupt_error_of(exc: BaseException) -> Optional[DaytonaError]:
    error = getattr(exc, _INTERRUPT_ERROR_ATTR, None)
    return error if isinstance(error, DaytonaError) else None
