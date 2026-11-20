// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
)

// Docker reads Docker Engine status; it never starts or restarts Docker.
// Obtain it through Sandbox.Docker.
type Docker struct{ sandbox *Sandbox }

// Docker returns the sandbox's read-only Docker Engine service.
func (s *Sandbox) Docker() *Docker { return &Docker{sandbox: s} }

// Status returns this boot's current Docker status without waiting.
// An unavailable endpoint returns ErrNotFound.
func (d *Docker) Status(ctx context.Context) (DockerStatus, error) {
	return d.readStatus(ctx, toolboxRequest{Path: "/docker/status"})
}

func (d *Docker) readStatus(ctx context.Context, request toolboxRequest) (DockerStatus, error) {
	var raw json.RawMessage
	spec := request
	spec.Result = &raw
	if err := d.sandbox.toolbox().do(ctx, spec); err != nil {
		return DockerStatus{}, err
	}
	return parseDockerStatus(raw)
}

func parseDockerStatus(raw json.RawMessage) (DockerStatus, error) {
	object, err := parseObject(raw, "docker status")
	if err != nil {
		return DockerStatus{}, err
	}
	var status DockerStatus
	for _, entry := range []struct {
		name     string
		target   *string
		required bool
	}{
		{"owner", &status.RawOwner, true},
		{"state", &status.RawState, true},
		{"processId", &status.ProcessID, false},
		{"reason", &status.Reason, false},
	} {
		value, fieldErr := field[string](object, entry.name, entry.required)
		if fieldErr != nil {
			return DockerStatus{}, fieldErr
		}
		*entry.target = value
	}
	switch status.RawOwner {
	case "none":
		status.Owner = DockerOwnerNone
	case "daemon":
		status.Owner = DockerOwnerDaemon
	case "entrypoint":
		status.Owner = DockerOwnerEntrypoint
	default:
		status.Owner = DockerOwnerUnknown
	}
	switch status.RawState {
	case "not_present":
		status.State = DockerStateNotPresent
	case "disabled":
		status.State = DockerStateDisabled
	case "unmanaged":
		status.State = DockerStateUnmanaged
	case "deferred":
		status.State = DockerStateDeferred
	case "starting":
		status.State = DockerStateStarting
	case "ready":
		status.State = DockerStateReady
	case "failed":
		status.State = DockerStateFailed
	case "unsupported":
		status.State = DockerStateUnsupported
	default:
		status.State = DockerStateUnknown
	}
	return status, nil
}
