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

// SandboxVolumes pulls attached volumes, distinct from Client.Volumes.
type SandboxVolumes struct{ sandbox *Sandbox }

// Volumes returns the sandbox's attached-volume pull service.
func (s *Sandbox) Volumes() *SandboxVolumes { return &SandboxVolumes{sandbox: s} }

// PullVolumeOptions configures one volume submission/check.
type PullVolumeOptions struct {
	// Timeout is the wait. Zero selects the server's 30-second default; NoWait
	// sends a zero wait. Up to 30 seconds is one request the sandbox holds for
	// that long; a longer wait is spent in rounds of at most 30 seconds with
	// the same request ID while the result is pending, and WaitForever (or a
	// duration above 2,147,483.647 seconds) has no deadline. Expiry does not
	// cancel work. Other negative values, including NoTimeout, return
	// ErrBadRequest.
	Timeout time.Duration
}

const (
	// pullVolumeRound is the longest server hold of one pull request (the
	// contract's waitMs maximum), so no request stays open for a longer wait.
	pullVolumeRound = 30 * time.Second
	// pullVolumeRoundMin paces rounds a sandbox answers pending early, so an
	// early answer never makes a busy loop.
	pullVolumeRoundMin = time.Second
)

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

// Pull submits/checks a pull, preserving requestID. Retain requestID for
// later checks or retries.
// volumeID and requestID require 1–128 ASCII letters, digits, "_" or "-".
// NoWait submits new work or retrieves a known identity without retrying it;
// during a positive wait the sandbox itself retries pending, unavailable or
// rate-limited work. A wait up to 30 seconds is one request; a longer one is
// spent in rounds of at most 30 seconds while the result is pending. The SDK
// never retries an error returned to it.
func (v *SandboxVolumes) Pull(
	ctx context.Context, volumeID, requestID string, opts *PullVolumeOptions,
) (*PullVolumeResult, error) {
	if err := parsePullVolumeID(volumeID, "volumeID"); err != nil {
		return nil, err
	}
	if err := parsePullVolumeID(requestID, "requestID"); err != nil {
		return nil, err
	}
	var timeout time.Duration
	if opts != nil {
		timeout = opts.Timeout
	}
	if timeout < 0 && timeout != NoWait && timeout != WaitForever {
		return nil, badRequest("Timeout must not be negative, except NoWait or WaitForever")
	}
	if timeout == WaitForever || timeout > pullVolumeRound {
		rounds := pullVolumeRounds{volumes: v, now: time.Now, pause: buildLogPause}
		return rounds.pull(ctx, volumeID, requestID, timeout)
	}
	return v.pullRound(ctx, volumeID, requestID, timeout)
}

// pullVolumeRounds waits a long pull out in rounds; now and pause are seams.
type pullVolumeRounds struct {
	volumes *SandboxVolumes
	now     func() time.Time
	pause   func(context.Context, time.Duration) error
}

func (r pullVolumeRounds) pull(ctx context.Context, volumeID, requestID string, timeout time.Duration,
) (*PullVolumeResult, error) {
	var deadline time.Time
	if timeout != WaitForever && timeout <= maxTimerDuration {
		deadline = r.now().Add(timeout)
	}
	for {
		started := r.now()
		hold := pullVolumeRound
		if !deadline.IsZero() {
			hold = max(min(hold, deadline.Sub(started)), time.Millisecond)
		}
		result, err := r.volumes.pullRound(ctx, volumeID, requestID, hold)
		if err != nil {
			return nil, err
		}
		now := r.now()
		if result.Status != PullVolumeStatusPending || !deadline.IsZero() && !now.Before(deadline) {
			return result, nil
		}
		if pause := pullVolumeRoundMin - now.Sub(started); pause > 0 {
			// Never return early: a pause cut at the deadline is followed by
			// one last check there.
			if !deadline.IsZero() {
				pause = min(pause, deadline.Sub(now))
			}
			if pauseErr := r.pause(ctx, pause); pauseErr != nil {
				return nil, transportError(ctx, pauseErr)
			}
		}
	}
}

// pullRound sends one request; timeout is zero (the server default), NoWait or at most one round.
func (v *SandboxVolumes) pullRound(ctx context.Context, volumeID, requestID string, timeout time.Duration,
) (*PullVolumeResult, error) {
	body := toolbox.PullVolumeRequest{RequestId: requestID}
	wait := pullVolumeRound
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
		return nil, err
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

func parsePullVolume(raw json.RawMessage) (*PullVolumeResult, error) {
	object, err := parseObject(raw, "volume pull")
	if err != nil {
		return nil, err
	}
	requestID, err := field[string](object, "requestId", true)
	if err != nil {
		return nil, err
	}
	status, err := field[string](object, "status", true)
	if err != nil {
		return nil, err
	}
	if _, exists := object["manifestId"]; !exists {
		return nil, badRequest("response field manifestId is required")
	}
	manifest, err := field[*string](object, "manifestId", false)
	if err != nil {
		return nil, err
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
	return &result, nil
}
