// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// VolumeFinalCommitState describes saving an ended sandbox's last volume writes.
type VolumeFinalCommitState string

const (
	// VolumeFinalCommitStateUnknown denotes a future server state; see RawState.
	VolumeFinalCommitStateUnknown VolumeFinalCommitState = "unknown"
	// VolumeFinalCommitStatePending means the save has not started.
	VolumeFinalCommitStatePending VolumeFinalCommitState = "pending"
	// VolumeFinalCommitStateSaving means the last writes are being saved.
	VolumeFinalCommitStateSaving VolumeFinalCommitState = "saving"
	// VolumeFinalCommitStateFailing means a failed save is being retried.
	VolumeFinalCommitStateFailing VolumeFinalCommitState = "failing"
)

// VolumePendingFinalCommit is an ended sandbox whose last writes are not saved.
type VolumePendingFinalCommit struct {
	// SandboxID identifies the ended sandbox.
	SandboxID string
	// State is the normalized saving status.
	State VolumeFinalCommitState
	// RawState is the server saving status verbatim.
	RawState string
	// EndingAt is when the sandbox ended.
	EndingAt time.Time
	// LastFailureAt is zero when saving has not failed.
	LastFailureAt time.Time
}

func parseVolumeFinalCommits(object jsonObject) ([]VolumePendingFinalCommit, error) {
	if _, exists := object["pending_final_commits"]; !exists {
		return []VolumePendingFinalCommit{}, nil
	}
	rows, err := field[[]JSONValue](object, "pending_final_commits", true)
	if err != nil {
		return nil, err
	}
	result := make([]VolumePendingFinalCommit, 0, len(rows))
	for _, row := range rows {
		entry, parseErr := parseVolumeFinalCommit(row)
		if parseErr != nil {
			return nil, parseErr
		}
		result = append(result, entry)
	}
	return result, nil
}

func parseVolumeFinalCommit(raw JSONValue) (VolumePendingFinalCommit, error) {
	var result VolumePendingFinalCommit
	object, err := parseObject(raw, "pending_final_commits item")
	if err != nil {
		return result, err
	}
	result.SandboxID, err = field[string](object, "sandbox_id", true)
	if err != nil {
		return result, err
	}
	result.RawState, err = field[string](object, "status", true)
	if err != nil {
		return result, err
	}
	switch result.RawState {
	case "pending":
		result.State = VolumeFinalCommitStatePending
	case "saving":
		result.State = VolumeFinalCommitStateSaving
	case "failing":
		result.State = VolumeFinalCommitStateFailing
	default:
		result.State = VolumeFinalCommitStateUnknown
	}
	if !present(object["ending_at"]) {
		return result, badRequest("response field ending_at is required")
	}
	result.EndingAt, err = timestampField(object, "ending_at")
	if err != nil {
		return result, err
	}
	if _, exists := object["last_failure_at"]; !exists {
		return result, badRequest("response field last_failure_at is required (nullable)")
	}
	result.LastFailureAt, err = timestampField(object, "last_failure_at")
	return result, err
}
