// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// SandboxState is the normalized lifecycle state; RawState keeps the wire value.
type SandboxState string

const (
	// SandboxStateUnknown is an unrecognized or not yet observed state.
	SandboxStateUnknown SandboxState = "unknown"
	// SandboxStateQueued means the sandbox is waiting for placement.
	SandboxStateQueued SandboxState = "queued"
	// SandboxStateCreating means provisioning is in progress.
	SandboxStateCreating SandboxState = "creating"
	// SandboxStateStarting means the sandbox is booting.
	SandboxStateStarting SandboxState = "starting"
	// SandboxStateStarted means the sandbox is running.
	SandboxStateStarted SandboxState = "started"
	// SandboxStatePausing means pause is in progress.
	SandboxStatePausing SandboxState = "pausing"
	// SandboxStatePaused means the sandbox is paused.
	SandboxStatePaused SandboxState = "paused"
	// SandboxStateStopping means stop is in progress.
	SandboxStateStopping SandboxState = "stopping"
	// SandboxStateStopped means the sandbox is stopped.
	SandboxStateStopped SandboxState = "stopped"
	// SandboxStateDestroying means deletion is in progress.
	SandboxStateDestroying SandboxState = "destroying"
	// SandboxStateDestroyed means the sandbox was deleted.
	SandboxStateDestroyed SandboxState = "destroyed"
	// SandboxStateError means the sandbox failed or was lost.
	SandboxStateError SandboxState = "error"
)

func sandboxState(raw string) SandboxState {
	switch raw {
	case "queued":
		return SandboxStateQueued
	case "pending", "assigned":
		return SandboxStateCreating
	case "booting":
		return SandboxStateStarting
	case "running":
		return SandboxStateStarted
	case "pausing":
		return SandboxStatePausing
	case "paused":
		return SandboxStatePaused
	case "stopping":
		return SandboxStateStopping
	case "stopped":
		return SandboxStateStopped
	case "deleting":
		return SandboxStateDestroying
	case "deleted":
		return SandboxStateDestroyed
	case "failed", "lost":
		return SandboxStateError
	default:
		return SandboxStateUnknown
	}
}

func stateWire(state SandboxState) ([]string, error) {
	switch state {
	case SandboxStateQueued:
		return []string{"queued"}, nil
	case SandboxStateCreating:
		return []string{"pending", "assigned"}, nil
	case SandboxStateStarting:
		return []string{"booting"}, nil
	case SandboxStateStarted:
		return []string{"running"}, nil
	case SandboxStatePausing:
		return []string{"pausing"}, nil
	case SandboxStatePaused:
		return []string{"paused"}, nil
	case SandboxStateStopping:
		return []string{"stopping"}, nil
	case SandboxStateStopped:
		return []string{"stopped"}, nil
	case SandboxStateDestroying:
		return []string{"deleting"}, nil
	case SandboxStateDestroyed:
		return []string{"deleted"}, nil
	case SandboxStateError:
		return []string{"failed", "lost"}, nil
	case SandboxStateUnknown:
		return nil, badRequest("unknown is not a searchable sandbox state")
	default:
		return nil, badRequest("invalid sandbox state " + string(state))
	}
}
