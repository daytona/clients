// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"go.daytona.com/sdk/v2/internal/gen/toolbox"
)

// SandboxVolumesService pulls attached volumes, distinct from Client.Volumes.
type SandboxVolumesService struct{ sandbox *Sandbox }

// Volumes returns the sandbox's attached-volume pull service.
func (s *Sandbox) Volumes() *SandboxVolumesService { return &SandboxVolumesService{sandbox: s} }

// PullVolumeOptions configures one volume submission/check.
type PullVolumeOptions struct {
	// Timeout is the server wait, at most 30 seconds. Zero selects the server's
	// 30-second default; NoWait sends a zero wait. Expiry does not cancel work.
	// Other negative sentinels, including WaitForever, return ErrBadRequest.
	Timeout time.Duration
}

// PullVolumeStatus is an attached-volume pull outcome.
type PullVolumeStatus string

const (
	// PullVolumeStatusUnknown preserves a future outcome in RawStatus.
	PullVolumeStatusUnknown PullVolumeStatus = "unknown"
	// PullVolumeStatusPulled means remote data was applied.
	PullVolumeStatusPulled PullVolumeStatus = "pulled"
	// PullVolumeStatusUnchanged means the remote manifest was unchanged.
	PullVolumeStatusUnchanged PullVolumeStatus = "unchanged"
	// PullVolumeStatusPending means accepted work has not completed.
	PullVolumeStatusPending PullVolumeStatus = "pending"
)

// PullVolumeResult is the outcome of one submission/check.
type PullVolumeResult struct {
	// ManifestID identifies remote data; nil means unavailable or pending.
	// An unchanged manifest does not imply local files are unchanged.
	ManifestID *string
	// RequestID is the server's echoed caller-owned identity.
	RequestID string
	// Status is the normalized outcome.
	Status PullVolumeStatus
	// RawStatus is the server's outcome verbatim.
	RawStatus string
}

// Pull submits/checks once, preserving requestID; pending results and errors
// never cause automatic retries. Retain requestID for later checks or retries.
// volumeID and requestID require 1–128 ASCII letters, digits, "_" or "-".
// NoWait submits new work or retrieves a known identity without retrying it;
// a positive wait retries pending, unavailable or rate-limited work.
func (v *SandboxVolumesService) Pull(
	ctx context.Context, volumeID, requestID string, opts *PullVolumeOptions,
) (PullVolumeResult, error) {
	if err := parsePullVolumeID(volumeID, "volumeID"); err != nil {
		return PullVolumeResult{}, err
	}
	if err := parsePullVolumeID(requestID, "requestID"); err != nil {
		return PullVolumeResult{}, err
	}
	var timeout time.Duration
	if opts != nil {
		timeout = opts.Timeout
	}
	if timeout < 0 && timeout != NoWait || timeout > 30*time.Second {
		return PullVolumeResult{}, badRequest("volume pull timeout must be in 0..30 seconds or NoWait")
	}
	body := toolbox.PullVolumeRequest{RequestId: requestID}
	wait := 30 * time.Second
	switch {
	case timeout == NoWait:
		wait = 0
		milliseconds := int64(0)
		body.WaitMs = &milliseconds
	case timeout > 0:
		milliseconds := max(int64(1), int64(timeout/time.Millisecond))
		body.WaitMs = &milliseconds
		wait = time.Duration(milliseconds) * time.Millisecond
	}
	var raw json.RawMessage
	err := v.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/volumes/" + volumeID + "/pull",
		Body: body, Timeout: wait + 5*time.Second, Result: &raw,
	})
	if err != nil {
		return PullVolumeResult{}, err
	}
	return parsePullVolume(raw)
}

func parsePullVolumeID(value, name string) error {
	if len(value) < 1 || len(value) > 128 || strings.ContainsFunc(value, func(r rune) bool {
		switch {
		case r >= 'A' && r <= 'Z', r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == '_', r == '-':
			return false
		default:
			return true
		}
	}) {
		return badRequest(name + " must contain 1–128 ASCII letters, digits, underscores or hyphens")
	}
	return nil
}

func parsePullVolume(raw json.RawMessage) (PullVolumeResult, error) {
	object, err := parseObject(raw, "volume pull")
	if err != nil {
		return PullVolumeResult{}, err
	}
	requestID, err := field[string](object, "requestId", true)
	if err != nil {
		return PullVolumeResult{}, err
	}
	status, err := field[string](object, "status", true)
	if err != nil {
		return PullVolumeResult{}, err
	}
	if _, exists := object["manifestId"]; !exists {
		return PullVolumeResult{}, badRequest("response field manifestId is required")
	}
	manifest, err := field[*string](object, "manifestId", false)
	if err != nil {
		return PullVolumeResult{}, err
	}
	result := PullVolumeResult{RequestID: requestID, ManifestID: manifest, RawStatus: status}
	switch status {
	case "pulled":
		result.Status = PullVolumeStatusPulled
	case "unchanged":
		result.Status = PullVolumeStatusUnchanged
	case "pending":
		result.Status = PullVolumeStatusPending
	default:
		result.Status = PullVolumeStatusUnknown
	}
	return result, nil
}
