// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "encoding/json"

// parseCreatedProcess retains only a validated identity if the full record is
// malformed. Callers must not treat this identity-only value as process metadata.
func parseCreatedProcess(raw json.RawMessage) (*ProcessInfo, error) {
	object, err := parseObject(raw, "process")
	if err != nil {
		return nil, err
	}
	id, err := field[string](object, "id", true)
	if err != nil {
		return nil, err
	}
	if err = checkResponseID(id); err != nil {
		return nil, err
	}
	info, err := parseProcess(raw)
	if err != nil {
		return &ProcessInfo{ID: id, Kind: ProcessKindUnknown}, err
	}
	return info, nil
}

// creationFailureResult distinguishes a malformed response with a usable
// identity from a failure before any process identity was received.
func (h *ProcessHandle) creationFailureResult() *ProcessResult {
	if h == nil {
		return nil
	}
	return processResult(h.ID(), WaitResult{Reason: ProcessReasonUnknown}, nil)
}
