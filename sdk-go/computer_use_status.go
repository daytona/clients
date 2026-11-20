// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// ComputerUseActivityStatus is the desktop activity state.
type ComputerUseActivityStatus string

const (
	// ComputerUseActivityUnknown denotes an unrecognized status/desktop pair.
	ComputerUseActivityUnknown ComputerUseActivityStatus = "unknown"
	// ComputerUseActivityActive means the desktop is ready.
	ComputerUseActivityActive ComputerUseActivityStatus = "active"
	// ComputerUseActivityInactive means the desktop is not ready.
	ComputerUseActivityInactive ComputerUseActivityStatus = "inactive"
)

// ComputerUseDesktopState is the observed desktop session state.
type ComputerUseDesktopState string

const (
	// ComputerUseDesktopUnknown denotes an unrecognized status/desktop pair.
	ComputerUseDesktopUnknown ComputerUseDesktopState = "unknown"
	// ComputerUseDesktopNotStarted means no desktop has started.
	ComputerUseDesktopNotStarted ComputerUseDesktopState = "not_started"
	// ComputerUseDesktopStarting means startup is in progress.
	ComputerUseDesktopStarting ComputerUseDesktopState = "starting"
	// ComputerUseDesktopReady means the desktop can accept input.
	ComputerUseDesktopReady ComputerUseDesktopState = "ready"
	// ComputerUseDesktopFailed means the session failed; inspect Cause.
	ComputerUseDesktopFailed ComputerUseDesktopState = "failed"
)

// ComputerUseDesktopFailureCode identifies the reason a desktop session failed.
type ComputerUseDesktopFailureCode string

const (
	// ComputerUseFailureUnknown preserves an unrecognized code in RawCode.
	ComputerUseFailureUnknown ComputerUseDesktopFailureCode = "unknown"
	// ComputerUseFailureDesktopExited means the desktop exited.
	ComputerUseFailureDesktopExited ComputerUseDesktopFailureCode = "desktop_exited"
	// ComputerUseFailureAddonCrashed means the addon crashed.
	ComputerUseFailureAddonCrashed ComputerUseDesktopFailureCode = "addon_crashed"
	// ComputerUseFailureStartFailed means startup failed.
	ComputerUseFailureStartFailed ComputerUseDesktopFailureCode = "start_failed"
	// ComputerUseFailureStartInterrupted means startup was interrupted.
	ComputerUseFailureStartInterrupted ComputerUseDesktopFailureCode = "start_interrupted"
	// ComputerUseFailureSessionEnded means the session ended.
	ComputerUseFailureSessionEnded ComputerUseDesktopFailureCode = "session_ended"
	// ComputerUseFailureSessionReplaced means another session replaced this one.
	ComputerUseFailureSessionReplaced ComputerUseDesktopFailureCode = "session_replaced"
	// ComputerUseFailureStateCorrupt means persisted session state is corrupt.
	ComputerUseFailureStateCorrupt ComputerUseDesktopFailureCode = "state_corrupt"
	// ComputerUseFailureStatePersistenceFailed means session state could not be saved.
	ComputerUseFailureStatePersistenceFailed ComputerUseDesktopFailureCode = "state_persistence_failed"
)

// ComputerUseDesktopFailure describes a failed session.
type ComputerUseDesktopFailure struct {
	// Code is the normalized failure code.
	Code ComputerUseDesktopFailureCode
	// RawCode is the server code verbatim.
	RawCode string
	// Message is the server explanation.
	Message string
	// ExtraFields preserves additional server fields.
	ExtraFields map[string]JSONValue
}

// ComputerUseStatus describes the last observed status/desktop pair.
// Known pairs are inactive/not_started, inactive/starting, active/ready and
// inactive/failed (the last requires Cause). Other pairs normalize both fields
// to Unknown, retaining raw values and any attached cause.
type ComputerUseStatus struct {
	// Status is the normalized activity.
	Status ComputerUseActivityStatus
	// Desktop is the normalized desktop state.
	Desktop ComputerUseDesktopState
	// RawStatus is the activity value verbatim.
	RawStatus string
	// RawDesktop is the desktop value verbatim.
	RawDesktop string
	// Cause is non-nil for failed sessions and may accompany unknown pairs.
	Cause *ComputerUseDesktopFailure
	// ExtraFields preserves additional server fields.
	ExtraFields map[string]JSONValue
}

// ComputerUseRestartResult reports a successfully restarted desktop.
type ComputerUseRestartResult struct {
	// Message is the server explanation.
	Message string
	// Status is active.
	Status ComputerUseActivityStatus
	// Desktop is ready.
	Desktop ComputerUseDesktopState
	// ExtraFields preserves additional server fields.
	ExtraFields map[string]JSONValue
}

func parseDesktopStatus(raw JSONValue) (*ComputerUseStatus, error) {
	d := decodeDesktop(raw)
	result := &ComputerUseStatus{
		RawStatus: d.text("status"), RawDesktop: d.text("desktop"),
		Status: ComputerUseActivityUnknown, Desktop: ComputerUseDesktopUnknown,
		ExtraFields: d.extras("status", "desktop", "cause"),
	}
	if present(d.object["cause"]) {
		cause, err := parseDesktopFailure(d.object["cause"])
		d.check(err)
		result.Cause = cause
	}
	switch result.RawStatus + "/" + result.RawDesktop {
	case "inactive/not_started", "inactive/starting", "active/ready":
		if result.Cause != nil {
			d.check(badRequest("response field cause must be absent for this status/desktop pair"))
		}
		result.Status = ComputerUseActivityStatus(result.RawStatus)
		result.Desktop = ComputerUseDesktopState(result.RawDesktop)
	case "inactive/failed":
		if result.Cause == nil {
			d.check(badRequest("response field cause is required for inactive/failed"))
		}
		result.Status, result.Desktop = ComputerUseActivityInactive, ComputerUseDesktopFailed
	}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

func parseDesktopFailure(raw JSONValue) (*ComputerUseDesktopFailure, error) {
	d := decodeDesktop(raw)
	result := &ComputerUseDesktopFailure{
		RawCode: d.text("code"), Message: d.text("message"), Code: ComputerUseFailureUnknown,
		ExtraFields: d.extras("code", "message"),
	}
	switch result.RawCode {
	case "desktop_exited", "addon_crashed", "start_failed", "start_interrupted",
		"session_ended", "session_replaced", "state_corrupt", "state_persistence_failed":
		result.Code = ComputerUseDesktopFailureCode(result.RawCode)
	}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

func parseDesktopRestart(raw JSONValue) (*ComputerUseRestartResult, error) {
	d := decodeDesktop(raw)
	result := &ComputerUseRestartResult{
		Message: d.text("message"), Status: ComputerUseActivityActive, Desktop: ComputerUseDesktopReady,
		ExtraFields: d.extras("message", "status", "desktop"),
	}
	if d.text("status") != "active" {
		d.check(badRequest("response field status must be active"))
	}
	if d.text("desktop") != "ready" {
		d.check(badRequest("response field desktop must be ready"))
	}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}
