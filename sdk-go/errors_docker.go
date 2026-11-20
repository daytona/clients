// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// DockerState is the last observed Docker Engine state.
type DockerState string

const (
	// DockerStateUnknown preserves an unrecognized wire state in RawState.
	DockerStateUnknown DockerState = "unknown"
	// DockerStateNotPresent means Docker is not installed.
	DockerStateNotPresent DockerState = "not_present"
	// DockerStateDisabled means Docker is disabled.
	DockerStateDisabled DockerState = "disabled"
	// DockerStateUnmanaged means another process manages Docker.
	DockerStateUnmanaged DockerState = "unmanaged"
	// DockerStateDeferred means startup has not begun.
	DockerStateDeferred DockerState = "deferred"
	// DockerStateStarting means startup is in progress.
	DockerStateStarting DockerState = "starting"
	// DockerStateReady means Docker is usable.
	DockerStateReady DockerState = "ready"
	// DockerStateFailed means startup failed.
	DockerStateFailed DockerState = "failed"
	// DockerStateUnsupported means Docker is unsupported.
	DockerStateUnsupported DockerState = "unsupported"
)

// DockerOwner identifies who manages Docker Engine.
type DockerOwner string

const (
	// DockerOwnerUnknown is an unrecognized owner.
	DockerOwnerUnknown DockerOwner = "unknown"
	// DockerOwnerNone means no process manages Docker.
	DockerOwnerNone DockerOwner = "none"
	// DockerOwnerDaemon means the daemon manages Docker.
	DockerOwnerDaemon DockerOwner = "daemon"
	// DockerOwnerEntrypoint means the entrypoint manages Docker.
	DockerOwnerEntrypoint DockerOwner = "entrypoint"
)

// DockerStatus is an observed Docker Engine status.
type DockerStatus struct {
	// Owner identifies the managing process.
	Owner DockerOwner
	// RawOwner is the server owner verbatim.
	RawOwner string
	// ProcessID identifies the process whose logs contain Docker output.
	// Nil means absent or null; a pointer to "" preserves an empty server value.
	ProcessID *string
	// Reason is the server's explanation. Nil means absent or null; a pointer
	// to "" preserves an empty server value.
	Reason *string
	// State is the normalized Docker state.
	State DockerState
	// RawState is the server state verbatim.
	RawState string
}

// DockerWaitTimeoutError carries the last status, if any, before a wait expired.
type DockerWaitTimeoutError struct {
	// SDKError is the underlying request timeout.
	SDKError *Error
	// Status is nil if no status was observed before timeout.
	Status *DockerStatus
}

// Error describes the wait timeout, even when SDKError is nil.
func (e *DockerWaitTimeoutError) Error() string {
	if e.SDKError == nil {
		return "daytona: Docker wait timed out"
	}
	return e.SDKError.Error()
}

// Unwrap exposes the request timeout, or nil when absent.
func (e *DockerWaitTimeoutError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}

// DockerNotReadyError carries a Docker state that cannot become ready this boot.
type DockerNotReadyError struct {
	// SDKError is the underlying conflict.
	SDKError *Error
	// Status is the terminal Docker status.
	Status DockerStatus
}

// Error describes the terminal Docker state, even when SDKError is nil.
func (e *DockerNotReadyError) Error() string {
	if e.SDKError == nil {
		return "daytona: Docker is not ready"
	}
	return e.SDKError.Error()
}

// Unwrap exposes the conflict, or nil when absent.
func (e *DockerNotReadyError) Unwrap() error {
	if e.SDKError == nil {
		return nil
	}
	return e.SDKError
}
