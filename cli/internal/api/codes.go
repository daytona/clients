// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

const (
	SourceAPI    = "DAYTONA_API"
	SourceDaemon = "DAYTONA_DAEMON"
	SourceProxy  = "DAYTONA_PROXY"
	SourceRouter = "DAYTONA_ROUTER"

	CodeForbidden                = "FORBIDDEN"
	CodeLimitExceeded            = "LIMIT_EXCEEDED"
	CodeOverloaded               = "OVERLOADED"
	CodeRateLimited              = "RATE_LIMITED"
	CodeSearchNotConfigured      = "SEARCH_NOT_CONFIGURED"
	CodeSearchUnavailable        = "SEARCH_UNAVAILABLE"
	CodeSignupNotInvited         = "SIGNUP_NOT_INVITED"
	CodeUserSuspended            = "USER_SUSPENDED"
	CodeBillingDisabled          = "BILLING_DISABLED"
	CodeBillingUnreachable       = "BILLING_UNREACHABLE"
	CodeEntitlementRequired      = "ENTITLEMENT_REQUIRED"
	CodeFeatureNotEnabled        = "FEATURE_NOT_ENABLED"
	CodeNotSupportedForClass     = "NOT_SUPPORTED_FOR_CLASS"
	CodeImageNotFound            = "IMAGE_NOT_FOUND"
	CodeImageNotReady            = "IMAGE_NOT_READY"
	CodeImageBuildFailed         = "IMAGE_BUILD_FAILED"
	CodeImageFormatOutdated      = "IMAGE_FORMAT_OUTDATED"
	CodeImageClassMismatch       = "IMAGE_CLASS_MISMATCH"
	CodeImageNameTaken           = "IMAGE_NAME_TAKEN"
	CodeImageNameReserved        = "IMAGE_NAME_RESERVED"
	CodeIdleOutOfRange           = "IDLE_OUT_OF_RANGE"
	CodeCreateTTLOutOfRange      = "CREATE_TTL_OUT_OF_RANGE"
	CodeSnapshotNotFound         = "SNAPSHOT_NOT_FOUND"
	CodeSnapshotNotReady         = "SNAPSHOT_NOT_READY"
	CodeSnapshotClassMismatch    = "SNAPSHOT_CLASS_MISMATCH"
	CodeSnapshotInProgress       = "SNAPSHOT_IN_PROGRESS"
	CodeProjectBudgetExceeded    = "PROJECT_BUDGET_EXCEEDED"
	CodeVolumeFinalCommitPending = "VOLUME_FINAL_COMMIT_PENDING"
	CodeVolumesUnavailable       = "VOLUMES_UNAVAILABLE"

	CodeProcessTerminal   = "PROCESS_TERMINAL"
	CodeCursorExpired     = "CURSOR_EXPIRED"
	CodeFileAccessDenied  = "FILE_ACCESS_DENIED"
	CodeSandboxNotRunning = "SANDBOX_NOT_RUNNING"

	CodeRegionMismatch         = "REGION_MISMATCH"
	CodeUnknownRegion          = "UNKNOWN_REGION"
	CodeRegionRequired         = "REGION_REQUIRED"
	CodeIdempotencyKeyRequired = "IDEMPOTENCY_KEY_REQUIRED"
)

var SpecialAPICodes = []string{
	CodeForbidden,
	CodeLimitExceeded,
	CodeOverloaded,
	CodeRateLimited,
	CodeSearchNotConfigured,
	CodeSearchUnavailable,
	CodeSignupNotInvited,
	CodeUserSuspended,
	CodeBillingDisabled,
	CodeBillingUnreachable,
	CodeEntitlementRequired,
	CodeFeatureNotEnabled,
	CodeNotSupportedForClass,
	CodeImageNotFound,
	CodeImageNotReady,
	CodeImageBuildFailed,
	CodeImageClassMismatch,
	CodeImageNameTaken,
	CodeImageNameReserved,
	CodeIdleOutOfRange,
	CodeCreateTTLOutOfRange,
	CodeSnapshotNotFound,
	CodeSnapshotNotReady,
	CodeSnapshotClassMismatch,
	CodeSnapshotInProgress,
	CodeProjectBudgetExceeded,
	CodeVolumeFinalCommitPending,
	CodeVolumesUnavailable,
}

var SpecialDaemonCodes = []string{
	CodeProcessTerminal,
	CodeCursorExpired,
	CodeFileAccessDenied,
}

var SpecialProxyCodes = []string{
	CodeSandboxNotRunning,
}

var SpecialRouterCodes = []string{
	CodeRegionMismatch,
	CodeUnknownRegion,
	CodeRegionRequired,
	CodeIdempotencyKeyRequired,
}
